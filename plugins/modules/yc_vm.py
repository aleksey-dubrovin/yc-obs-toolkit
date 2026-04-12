#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
module: yc_vm
short_description: Create virtual machines in Yandex Cloud with auto-numbering
'''

import os
import re
import subprocess
import json
from ansible.module_utils.basic import AnsibleModule

def run_yc_command(module, cmd, error_msg):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"{error_msg}: {e.stderr}")

def get_next_vm_number(module, name_prefix, folder_id):
    """Находит максимальный номер ВМ с заданным префиксом и возвращает следующий номер"""
    cmd = [
        'yc', 'compute', 'instance', 'list',
        '--folder-id', folder_id,
        '--format', 'json'
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        vms = json.loads(result.stdout)
        
        max_num = 0
        pattern = re.compile(rf'^{name_prefix}-(\d+)$')
        
        for vm in vms:
            name = vm.get('name', '')
            match = pattern.match(name)
            if match:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num
        
        return max_num + 1
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return 1

def get_image_id(module, image_family):
    cmd = ['yc', 'compute', 'image', 'get-latest-from-family', image_family,
           '--folder-id', 'standard-images', '--format', 'json']
    stdout = run_yc_command(module, cmd, f"Failed to get image for family '{image_family}'")
    try:
        return json.loads(stdout)['id']
    except (json.JSONDecodeError, KeyError) as e:
        module.fail_json(msg=f"Failed to parse image info: {e}")

def vm_exists(module, name, folder_id):
    cmd = ['yc', 'compute', 'instance', 'get', '--name', name,
           '--folder-id', folder_id, '--format', 'json']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        return None
    except Exception:
        return None

def delete_vm(module, name, folder_id):
    cmd = ['yc', 'compute', 'instance', 'delete', '--name', name, '--folder-id', folder_id]
    run_yc_command(module, cmd, f"Failed to delete VM '{name}'")

def create_vm(module, params, image_id, ssh_key_path, vm_number):
    """Создаёт ВМ с автоматическим номером"""
    full_name = f"{params['name_prefix']}-{vm_number}"
    
    boot_disk_params = [f"image-id={image_id}", f"size={params['disk_size']}", f"type={params['disk_type']}"]
    cmd = [
        'yc', 'compute', 'instance', 'create',
        '--name', full_name,
        '--folder-id', params['folder_id'],
        '--zone', params['zone'],
        '--platform-id', params['platform_id'],
        '--cores', str(params['cores']),
        '--memory', str(params['memory']),
        '--core-fraction', str(params['core_fraction']),
        '--create-boot-disk', ','.join(boot_disk_params),
        '--network-interface', f"subnet-name={params['subnet_name']},nat-ip-version={'ipv4' if params['assign_public_ip'] else 'none'}",
        '--ssh-key', ssh_key_path,
        '--format', 'json'
    ]
    
    if params.get('labels'):
        for key, value in params['labels'].items():
            cmd.extend(['--labels', f"{key}={value}"])
    
    if params['preemptible']:
        cmd.append('--preemptible')
    
    stdout = run_yc_command(module, cmd, f"Failed to create VM '{full_name}'")
    try:
        instance_info = json.loads(stdout)
        interfaces = instance_info.get('network_interfaces', [])
        if interfaces and params['assign_public_ip']:
            nat = interfaces[0].get('primary_v4_address', {}).get('one_to_one_nat', {})
            instance_info['external_ip'] = nat.get('address')
        instance_info['generated_name'] = full_name
        instance_info['vm_number'] = vm_number
        return instance_info
    except json.JSONDecodeError as e:
        module.fail_json(msg=f"Failed to parse VM info: {e}")

def run_module():
    module_args = dict(
        name_prefix=dict(type='str', required=True),
        folder_id=dict(type='str', required=True),
        zone=dict(type='str', default='ru-central1-b'),
        subnet_name=dict(type='str', required=True),
        image_family=dict(type='str', default='ubuntu-2204-lts'),
        platform_id=dict(type='str', default='standard-v3'),
        cores=dict(type='int', default=2),
        memory=dict(type='int', default=4),
        core_fraction=dict(type='int', default=20),
        disk_size=dict(type='int', default=20),
        disk_type=dict(type='str', default='network-hdd'),
        assign_public_ip=dict(type='bool', default=True),
        preemptible=dict(type='bool', default=True),
        ssh_key_path=dict(type='str', default='~/.ssh/id_rsa.pub'),
        vm_user=dict(type='str', default='ubuntu'),
        service_account_key=dict(type='str', required=False, no_log=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        labels=dict(type='dict', required=False, default={})
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    result = dict(changed=False, instance_info=None)

    # Проверка YC CLI
    try:
        subprocess.run(['yc', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        module.fail_json(msg="YC CLI is not installed or not in PATH.")

    if module.params['service_account_key']:
        os.environ['YC_SERVICE_ACCOUNT_KEY_FILE'] = module.params['service_account_key']

    cmd = ['yc', 'resource-manager', 'folder', 'get', module.params['folder_id'], '--format', 'json']
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Not authenticated or no access to folder: {e.stderr}")

    ssh_key_path = os.path.expanduser(module.params['ssh_key_path'])
    if not os.path.exists(ssh_key_path):
        module.fail_json(msg=f"SSH key not found: {ssh_key_path}")

    # Получаем следующий номер для ВМ
    vm_number = get_next_vm_number(module, module.params['name_prefix'], module.params['folder_id'])
    full_name = f"{module.params['name_prefix']}-{vm_number}"
    
    existing_vm = vm_exists(module, full_name, module.params['folder_id'])

    if module.params['state'] == 'absent':
        if existing_vm:
            if not module.check_mode:
                delete_vm(module, full_name, module.params['folder_id'])
                result['changed'] = True
                result['instance_info'] = {'status': 'deleted', 'name': full_name}
            else:
                result['changed'] = True
        module.exit_json(**result)

    if existing_vm:
        result['instance_info'] = existing_vm
        result['instance_info']['vm_number'] = vm_number
        result['changed'] = False
        module.exit_json(**result)

    if not module.check_mode:
        image_id = get_image_id(module, module.params['image_family'])
        instance_info = create_vm(module, module.params, image_id, ssh_key_path, vm_number)
        result['instance_info'] = instance_info
        result['changed'] = True
    else:
        result['changed'] = True

    module.exit_json(**result)

def main():
    run_module()

if __name__ == '__main__':
    main()