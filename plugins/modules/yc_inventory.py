#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, Aleksey Dubrovin
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
---
module: yc_inventory
short_description: Generate dynamic inventory from Yandex Cloud VMs
description:
  - Retrieves information about VMs in Yandex Cloud and generates an inventory.
  - Can return inventory as a dictionary or write to a YAML file.
  - Groups VMs based on labels (ansible_group, role, app).
  - Requires YC CLI installed and authenticated.
options:
  folder_id:
    description: ID of the folder in Yandex Cloud.
    required: true
    type: str
  service_account_key:
    description: Path to service account key file for authentication.
    required: false
    type: str
  output_file:
    description: Path to write the inventory file (YAML format).
    required: false
    type: str
  group_by:
    description: List of label keys to group VMs by.
    default: ['ansible_group', 'role', 'app']
    type: list
    elements: str
  include_ungrouped:
    description: Include VMs without matching groups in 'ungrouped'.
    default: true
    type: bool
  ansible_user:
    description: Default SSH user for VMs (can be overridden by image detection).
    default: rocky
    type: str
  state:
    description: Desired state (present - generate inventory, absent - remove inventory file).
    default: present
    type: str
    choices: ['present', 'absent']
author:
  - Aleksey Dubrovin (@aleksey-dubrovin)
'''

EXAMPLES = r'''
- name: Generate inventory and return as dictionary
  aleksey_dubrovin.yandex_cloud_elk.yc_inventory:
    folder_id: "{{ yc_folder_id }}"
  register: inventory

- name: Generate inventory and save to file
  aleksey_dubrovin.yandex_cloud_elk.yc_inventory:
    folder_id: "{{ yc_folder_id }}"
    output_file: "{{ inventory_file_path }}"

- name: Generate inventory with custom grouping
  aleksey_dubrovin.yandex_cloud_elk.yc_inventory:
    folder_id: "{{ yc_folder_id }}"
    group_by:
      - environment
      - service
    output_file: inventory.yml
'''

RETURN = r'''
inventory:
  description: Dictionary representing the generated inventory.
  type: dict
  returned: when output_file is not specified
  sample: |
    {
      "all": {
        "children": {
          "clickhouse": {
            "hosts": {
              "51.250.1.2": {}
            }
          }
        }
      }
    }
output_file:
  description: Path to the generated inventory file.
  type: str
  returned: when output_file is specified
changed:
  description: Whether the inventory was generated or file was created/deleted.
  type: bool
'''

import os
import subprocess
import json
import yaml
from ansible.module_utils.basic import AnsibleModule


def check_yc_cli(module):
    """Check if YC CLI is installed and accessible"""
    try:
        result = subprocess.run(['yc', '--version'], capture_output=True, text=True)
        if result.returncode != 0:
            module.fail_json(msg="YC CLI is not working properly. Run 'yc --version' to debug.")
        return True
    except FileNotFoundError:
        module.fail_json(
            msg="YC CLI is not installed or not in PATH.\n"
                "Installation instructions: https://cloud.yandex.ru/docs/cli/quickstart"
        )
    except Exception as e:
        module.fail_json(msg=f"Unexpected error checking YC CLI: {str(e)}")


def check_yc_auth(module, folder_id):
    """Check if YC CLI is authenticated and has access to the folder"""
    cmd = ['yc', 'resource-manager', 'folder', 'get', folder_id, '--format', 'json']
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        module.fail_json(
            msg=f"Not authenticated or no access to folder '{folder_id}'.\n"
                f"Error: {e.stderr}\n"
                "Run 'yc init' or 'yc auth activate-service-account --key key.json'"
        )
    except Exception as e:
        module.fail_json(msg=f"Unexpected error checking authentication: {str(e)}")


def get_vms(module, folder_id):
    """Get all VMs from Yandex Cloud"""
    cmd = ['yc', 'compute', 'instance', 'list', '--folder-id', folder_id, '--format', 'json']

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Failed to get VMs: {e.stderr}")
    except json.JSONDecodeError as e:
        module.fail_json(msg=f"Failed to parse YC output: {e}")


def get_vm_connection_info(vm, default_user):
    """Extract connection information from VM"""
    external_ip = None
    internal_ip = None

    interfaces = vm.get('network_interfaces', [])
    if interfaces:
        nat = interfaces[0].get('primary_v4_address', {}).get('one_to_one_nat', {})
        external_ip = nat.get('address')
        internal_ip = interfaces[0].get('primary_v4_address', {}).get('address')

    # Определяем пользователя по образу
    image_info = vm.get('image', {})
    image_name = image_info.get('name', '').lower()

    if 'ubuntu' in image_name:
        vm_user = 'ubuntu'
    elif 'rocky' in image_name or 'centos' in image_name:
        vm_user = 'rocky'
    elif 'debian' in image_name:
        vm_user = 'debian'
    else:
        vm_user = default_user

    return {
        'external_ip': external_ip,
        'internal_ip': internal_ip,
        'ansible_host': external_ip or internal_ip,
        'ansible_user': vm_user
    }


def build_inventory(vms, group_by, include_ungrouped, default_user):
    """Build inventory dictionary from VMs"""
    inventory = {'all': {'children': {}, 'hosts': {}}}

    for vm in vms:
        if vm.get('status') != 'RUNNING':
            continue

        connection_info = get_vm_connection_info(vm, default_user)

        if not connection_info['ansible_host']:
            continue

        labels = vm.get('labels', {})

        # Определяем группы
        groups = []
        for group_key in group_by:
            if group_key in labels and labels[group_key]:
                groups.append(labels[group_key])

        if not groups and include_ungrouped:
            groups = ['ungrouped']

        for group in groups:
            if group not in inventory['all']['children']:
                inventory['all']['children'][group] = {'hosts': {}}

            host_entry = {
                'ansible_host': connection_info['ansible_host'],
                'ansible_user': connection_info['ansible_user'],
                'vm_name': vm.get('name'),
                'vm_id': vm.get('id'),
                'internal_ip': connection_info['internal_ip'],
                'external_ip': connection_info['external_ip']
            }

            inventory['all']['children'][group]['hosts'][connection_info['ansible_host']] = host_entry

    return inventory


def run_module():
    module_args = dict(
        folder_id=dict(type='str', required=True),
        service_account_key=dict(type='str', required=False, no_log=True),
        output_file=dict(type='str', required=False),
        group_by=dict(type='list', elements='str', default=['ansible_group', 'role', 'app']),
        include_ungrouped=dict(type='bool', default=True),
        ansible_user=dict(type='str', default='rocky'),
        state=dict(type='str', default='present', choices=['present', 'absent'])
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    result = dict(
        changed=False,
        inventory=None,
        output_file=None
    )

    # Проверка YC CLI и аутентификации
    check_yc_cli(module)

    if module.params['service_account_key']:
        os.environ['YC_SERVICE_ACCOUNT_KEY_FILE'] = module.params['service_account_key']

    check_yc_auth(module, module.params['folder_id'])

    # Удаление файла инвентаря
    if module.params['state'] == 'absent':
        if module.params['output_file'] and os.path.exists(module.params['output_file']):
            if not module.check_mode:
                os.remove(module.params['output_file'])
                result['changed'] = True
                result['output_file'] = module.params['output_file']
        module.exit_json(**result)

    # Генерация инвентаря
    vms = get_vms(module, module.params['folder_id'])
    inventory = build_inventory(
        vms,
        module.params['group_by'],
        module.params['include_ungrouped'],
        module.params['ansible_user']
    )
    result['inventory'] = inventory
    result['changed'] = True

    # Запись в файл, если указан
    if module.params['output_file']:
        if not module.check_mode:
            output_dir = os.path.dirname(module.params['output_file'])
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            with open(module.params['output_file'], 'w') as f:
                yaml.dump(inventory, f, default_flow_style=False)

            result['output_file'] = module.params['output_file']
            result['inventory'] = None
    else:
        # Если файл не указан, возвращаем инвентарь в ответе
        result['inventory'] = inventory

    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()