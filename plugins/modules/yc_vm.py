#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, Aleksey Dubrovin
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
---
module: yc_vm
short_description: Create and manage virtual machines in Yandex Cloud
description:
  - This module creates, updates, and deletes virtual machines in Yandex Cloud.
  - It uses the YC CLI under the hood and is idempotent.
  - Requires YC CLI installed and authenticated.
options:
  name:
    description: Name of the virtual machine.
    required: true
    type: str
  folder_id:
    description: ID of the folder in Yandex Cloud.
    required: true
    type: str
  zone:
    description: Availability zone (e.g., ru-central1-b, ru-central1-a, ru-central1-c).
    default: ru-central1-b
    type: str
  subnet_name:
    description: Name of the subnet for the VM.
    required: true
    type: str
  image_family:
    description: Family of the image (e.g., rocky-9-oslogin, ubuntu-2204-lts).
    default: rocky-9-oslogin
    type: str
  platform_id:
    description: Hardware platform (standard-v1, standard-v2, standard-v3).
    default: standard-v3
    type: str
  cores:
    description: Number of CPU cores.
    default: 2
    type: int
  memory:
    description: Memory in GB.
    default: 4
    type: int
  core_fraction:
    description: Core fraction (20, 50, 100).
    default: 20
    type: int
  disk_size:
    description: Disk size in GB.
    default: 10
    type: int
  disk_type:
    description: Disk type (network-hdd, network-ssd).
    default: network-hdd
    type: str
  assign_public_ip:
    description: Assign a public IP address.
    default: true
    type: bool
  preemptible:
    description: Use preemptible VM (cheaper but can be terminated).
    default: true
    type: bool
  ssh_key_path:
    description: Path to the public SSH key file.
    default: ~/.ssh/id_rsa.pub
    type: str
  vm_user:
    description: Username for SSH access.
    default: rocky
    type: str
  service_account_key:
    description: Path to service account key file for authentication.
    required: false
    type: str
  state:
    description: Desired state of the VM (present, absent).
    default: present
    type: str
    choices: ['present', 'absent']
author:
  - Aleksey Dubrovin (@aleksey-dubrovin)
'''

EXAMPLES = r'''
- name: Create a VM in Yandex Cloud
  aleksey_dubrovin.yandex_cloud_elk.yc_vm:
    name: "{{ vm_name }}"
    folder_id: "{{ yc_folder_id }}"
    subnet_name: "{{ yc_subnet_name }}"

- name: Create a VM with custom specs
  aleksey_dubrovin.yandex_cloud_elk.yc_vm:
    name: "{{ vm_name }}"
    folder_id: "{{ yc_folder_id }}"
    subnet_name: "{{ yc_subnet_name }}"
    cores: 4
    memory: 8
    preemptible: false

- name: Delete a VM
  aleksey_dubrovin.yandex_cloud_elk.yc_vm:
    name: "{{ vm_name }}"
    folder_id: "{{ yc_folder_id }}"
    state: absent
'''

RETURN = r'''
instance_info:
  description: Information about the created or existing VM.
  type: dict
  returned: always
changed:
  description: Whether the VM was created, modified, or deleted.
  type: bool
  returned: always
'''

import os
import subprocess
import json
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


def get_image_id(module, image_family):
    """Get image ID from image family"""
    cmd = [
        'yc', 'compute', 'image', 'get-latest-from-family',
        image_family,
        '--folder-id', 'standard-images',
        '--format', 'json'
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        image_info = json.loads(result.stdout)
        return image_info['id']
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Failed to get image for family '{image_family}': {e.stderr}")
    except json.JSONDecodeError as e:
        module.fail_json(msg=f"Failed to parse YC output for image: {str(e)}")


def vm_exists(module, name, folder_id):
    """Check if VM already exists"""
    cmd = [
        'yc', 'compute', 'instance', 'get',
        '--name', name,
        '--folder-id', folder_id,
        '--format', 'json'
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        return None
    except Exception:
        return None


def delete_vm(module, name, folder_id):
    """Delete VM by name"""
    cmd = [
        'yc', 'compute', 'instance', 'delete',
        '--name', name,
        '--folder-id', folder_id
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Failed to delete VM '{name}': {e.stderr}")
    return False


def create_vm(module, params, image_id, ssh_key_path):
    """Create a new VM with given parameters"""
    cmd = [
        'yc', 'compute', 'instance', 'create',
        '--name', params['name'],
        '--folder-id', params['folder_id'],
        '--zone', params['zone'],
        '--platform-id', params['platform_id'],
        '--cores', str(params['cores']),
        '--memory', str(params['memory']),
        '--core-fraction', str(params['core_fraction']),
        '--disk-size', str(params['disk_size']),
        '--disk-type', params['disk_type'],
        '--image-id', image_id,
        '--network-interface', f"subnet-name={params['subnet_name']},nat-ip-version={'ipv4' if params['assign_public_ip'] else 'none'}",
        '--ssh-key', ssh_key_path,
        '--format', 'json'
    ]

    if params['preemptible']:
        cmd.append('--preemptible')

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Failed to create VM '{params['name']}': {e.stderr}")
    except json.JSONDecodeError as e:
        module.fail_json(msg=f"Failed to parse YC output for VM creation: {str(e)}")
    return None


def run_module():
    module_args = dict(
        name=dict(type='str', required=True),
        folder_id=dict(type='str', required=True),
        zone=dict(type='str', default='ru-central1-b'),
        subnet_name=dict(type='str', required=True),
        image_family=dict(type='str', default='rocky-9-oslogin'),
        platform_id=dict(type='str', default='standard-v3'),
        cores=dict(type='int', default=2),
        memory=dict(type='int', default=4),
        core_fraction=dict(type='int', default=20),
        disk_size=dict(type='int', default=10),
        disk_type=dict(type='str', default='network-hdd'),
        assign_public_ip=dict(type='bool', default=True),
        preemptible=dict(type='bool', default=True),
        ssh_key_path=dict(type='str', default='~/.ssh/id_rsa.pub'),
        vm_user=dict(type='str', default='rocky'),
        service_account_key=dict(type='str', required=False, no_log=True),
        state=dict(type='str', default='present', choices=['present', 'absent'])
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    result = dict(
        changed=False,
        instance_info=None
    )

    # Проверка YC CLI и аутентификации
    check_yc_cli(module)

    # Настройка аутентификации через сервисный аккаунт если указан
    if module.params['service_account_key']:
        os.environ['YC_SERVICE_ACCOUNT_KEY_FILE'] = module.params['service_account_key']

    check_yc_auth(module, module.params['folder_id'])

    ssh_key_path = os.path.expanduser(module.params['ssh_key_path'])
    if not os.path.exists(ssh_key_path):
        module.fail_json(msg=f"SSH key file not found: {ssh_key_path}")

    # Проверяем существование ВМ
    existing_vm = vm_exists(module, module.params['name'], module.params['folder_id'])

    # Удаление ВМ
    if module.params['state'] == 'absent':
        if existing_vm:
            if not module.check_mode:
                delete_vm(module, module.params['name'], module.params['folder_id'])
                result['changed'] = True
                result['instance_info'] = {'status': 'deleted', 'name': module.params['name']}
            else:
                result['changed'] = True
        module.exit_json(**result)

    # Если ВМ существует — возвращаем информацию
    if existing_vm:
        result['instance_info'] = existing_vm
        result['changed'] = False
        module.exit_json(**result)

    # Создание ВМ
    if not module.check_mode:
        image_id = get_image_id(module, module.params['image_family'])
        instance_info = create_vm(module, module.params, image_id, ssh_key_path)

        # Добавляем внешний IP для удобства
        try:
            interfaces = instance_info.get('network_interfaces', [])
            if interfaces and module.params['assign_public_ip']:
                nat = interfaces[0].get('primary_v4_address', {}).get('one_to_one_nat', {})
                instance_info['external_ip'] = nat.get('address')
        except Exception:
            pass

        result['instance_info'] = instance_info
        result['changed'] = True
    else:
        result['changed'] = True

    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()