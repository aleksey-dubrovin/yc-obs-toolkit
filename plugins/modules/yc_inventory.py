#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
module: yc_inventory
short_description: Generate dynamic inventory from Yandex Cloud VMs
description:
  - Generates Ansible inventory from Yandex Cloud VMs in the correct format.
options:
  folder_id:
    description: ID of the folder in Yandex Cloud.
    required: true
    type: str
  service_account_key:
    description: Path to service account key file.
    required: false
    type: str
  output_dir:
    description: Directory to save the inventory file.
    default: inventory
    type: str
  ansible_user:
    description: Default SSH user for VMs.
    default: rocky
    type: str
  ansible_ssh_private_key_file:
    description: Path to SSH private key.
    default: ~/.ssh/id_rsa
    type: str
  group_by:
    description: List of label keys to group by.
    default: ['ansible_group', 'role', 'app']
    type: list
    elements: str
  state:
    description: Desired state.
    default: present
    type: str
    choices: ['present', 'absent']
author:
  - Aleksey Dubrovin (@aleksey-dubrovin)
'''

import os
import subprocess
import json
import yaml
from ansible.module_utils.basic import AnsibleModule


def run_yc_command(module, cmd, error_msg):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"{error_msg}: {e.stderr}")


def get_vms(module, folder_id):
    cmd = ['yc', 'compute', 'instance', 'list', '--folder-id', folder_id, '--format', 'json']
    stdout = run_yc_command(module, cmd, "Failed to get VMs list")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        module.fail_json(msg=f"Failed to parse VMs list: {e}")


def get_vm_info(vm, ansible_user, ssh_private_key):
    """Extract VM information in the correct format"""
    interfaces = vm.get('network_interfaces', [])
    external_ip = None
    internal_ip = None
    fqdn = None
    
    if interfaces:
        nat = interfaces[0].get('primary_v4_address', {}).get('one_to_one_nat', {})
        external_ip = nat.get('address')
        internal_ip = interfaces[0].get('primary_v4_address', {}).get('address')
        fqdn = interfaces[0].get('primary_v4_address', {}).get('dns_records', [{}])[0].get('fqdn')
    
    resources = vm.get('resources', {})
    
    return {
        'ansible_host': external_ip or internal_ip,
        'ansible_user': ansible_user,
        'ansible_ssh_private_key_file': ssh_private_key,
        'private_ip': internal_ip,
        'public_ip': external_ip,
        'fqdn': fqdn,
        'vm_id': vm.get('id'),
        'zone': vm.get('zone_id'),
        'status': vm.get('status'),
        'created_at': vm.get('created_at'),
        'cores': resources.get('cores'),
        'memory_gb': float(resources.get('memory', 0)) / (1024**3) if resources.get('memory') else None,
        'labels': vm.get('labels', {})
    }


def build_inventory(vms, ansible_user, ssh_private_key, group_by):
    """Build inventory dictionary in the correct format"""
    inventory = {
        'all': {
            'vars': {
                'ansible_connection': 'ssh',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no',
                'ansible_user': ansible_user,
                'ansible_ssh_private_key_file': ssh_private_key
            }
        },
        'all_vms': {'hosts': {}},
        'children': {}
    }
    
    # Словарь для отслеживания групп
    groups = {}
    
    for vm in vms:
        if vm.get('status') != 'RUNNING':
            continue
        
        vm_name = vm.get('name')
        vm_info = get_vm_info(vm, ansible_user, ssh_private_key)
        
        if not vm_info['ansible_host']:
            continue
        
        # Добавляем в all_vms
        inventory['all_vms']['hosts'][vm_name] = vm_info
        
        labels = vm.get('labels', {})
        
        # Группировка по меткам
        for group_key in group_by:
            if group_key in labels and labels[group_key]:
                group_name = labels[group_key]
                if group_name not in groups:
                    groups[group_name] = {'hosts': {}}
                groups[group_name]['hosts'][vm_name] = vm_info
        
        # Группировка по ролям (role_*)
        if 'role' in labels and labels['role']:
            role_group = f"role_{labels['role']}"
            if role_group not in groups:
                groups[role_group] = {'hosts': {}}
            groups[role_group]['hosts'][vm_name] = vm_info
    
    # Объединяем все группы в инвентарь
    for group_name, group_data in groups.items():
        inventory[group_name] = group_data
    
    return inventory


def run_module():
    module_args = dict(
        folder_id=dict(type='str', required=True),
        service_account_key=dict(type='str', required=False, no_log=True),
        output_dir=dict(type='str', default='inventory'),
        ansible_user=dict(type='str', default='rocky'),
        ansible_ssh_private_key_file=dict(type='str', default='~/.ssh/id_rsa'),
        group_by=dict(type='list', elements='str', default=['ansible_group', 'role', 'app']),
        state=dict(type='str', default='present', choices=['present', 'absent'])
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    result = dict(changed=False, inventory=None, output_file=None)

    # Проверка YC CLI
    try:
        subprocess.run(['yc', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        module.fail_json(msg="YC CLI is not installed or not in PATH.")

    if module.params['service_account_key']:
        os.environ['YC_SERVICE_ACCOUNT_KEY_FILE'] = module.params['service_account_key']

    # Проверка аутентификации
    cmd = ['yc', 'resource-manager', 'folder', 'get', module.params['folder_id'], '--format', 'json']
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Not authenticated or no access to folder: {e.stderr}")

    # Разворачиваем путь к SSH ключу
    ssh_private_key = os.path.expanduser(module.params['ansible_ssh_private_key_file'])
    
    # Создаём директорию inventory
    inventory_dir = module.params['output_dir']
    if not os.path.exists(inventory_dir):
        os.makedirs(inventory_dir, exist_ok=True)
    
    inventory_file = os.path.join(inventory_dir, 'inventory.yml')
    
    # Удаление файла инвентаря
    if module.params['state'] == 'absent':
        if os.path.exists(inventory_file):
            if not module.check_mode:
                os.remove(inventory_file)
                result['changed'] = True
                result['output_file'] = inventory_file
        module.exit_json(**result)
    
    # Получаем ВМ и строим инвентарь
    vms = get_vms(module, module.params['folder_id'])
    inventory = build_inventory(
        vms,
        module.params['ansible_user'],
        ssh_private_key,
        module.params['group_by']
    )
    
    result['inventory'] = inventory
    result['changed'] = True
    
    if not module.check_mode:
        # Добавляем заголовок
        inventory_content = yaml.dump(inventory, default_flow_style=False, allow_unicode=True)
        with open(inventory_file, 'w') as f:
            f.write(f"# Ansible inventory generated by yc_inventory module\n")
            f.write(f"# Total VMs: {len([v for v in vms if v.get('status') == 'RUNNING'])}\n\n")
            f.write(inventory_content)
        
        result['output_file'] = inventory_file
        result['inventory'] = None
    
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()