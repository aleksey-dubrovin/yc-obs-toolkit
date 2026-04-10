#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
---
module: yc_vm
short_description: Create and manage virtual machines in Yandex Cloud
description:
  - This module creates, updates, and deletes virtual machines in Yandex Cloud.
  - It uses the YC CLI under the hood and is idempotent.
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
    description: Availability zone.
    default: ru-central1-b
    type: str
  subnet_name:
    description: Name of the subnet for the VM.
    required: true
    type: str
  image_family:
    description: Family of the image.
    default: rocky-9-oslogin
    type: str
  platform_id:
    description: Hardware platform.
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
    description: Use preemptible VM.
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
    description: Path to service account key file.
    required: false
    type: str
  state:
    description: Desired state of the VM.
    default: present
    type: str
    choices: ['present', 'absent']
author:
  - Aleksey Dubrovin (@aleksey-dubrovin)
'''

import os
import subprocess
import json
from ansible.module_utils.basic import AnsibleModule


def check_yc_cli(module):
    try:
        subprocess.run(['yc', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        module.fail_json(msg="YC CLI is not installed or not in PATH.")


def check_yc_auth(module, folder_id):
    cmd = ['yc', 'resource-manager', 'folder', 'get', folder_id, '--format', 'json']
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Not authenticated or no access to folder '{folder_id}': {e.stderr}")


def get_image_id(module, image_family):
    cmd = ['yc', 'compute', 'image', 'get-latest-from-family', image_family,
           '--folder-id', 'standard-images', '--format', 'json']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)['id']
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Failed to get image: {e.stderr}")


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
    cmd = ['yc', 'compute', 'instance', 'delete', '--name', name,
           '--folder-id', folder_id]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Failed to delete VM: {e.stderr}")


def create_vm(module, params, image_id, ssh_key_path):
    boot_disk_params = [
    f"image-id={image_id}",
    f"size={params['disk_size']}",
    f"type={params['disk_type']}"
    ]
    
    cmd = [
        'yc', 'compute', 'instance', 'create',
        '--name', params['name'],
        '--folder-id', params['folder_id'],
        '--zone', params['zone'],
        '--platform-id', params['platform_id'],
        '--cores', str(params['cores']),
        '--memory', str(params['memory']),
        '--core-fraction', str(params['core_fraction']),
        '--create-boot-disk', ','.join(boot_disk_params),
        '--image-id', image_id,
        '--network-interface', f"subnet-name={params['subnet_name']},nat-ip-version={'ipv4' if params['assign_public_ip'] else 'none'}",
        '--ssh-key', ssh_key_path,
        '--format', 'json'
    ]
    if params['preemptible']:
        cmd.append('--preemptible')
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        instance_info = json.loads(result.stdout)
        # Добавляем внешний IP для удобства
        try:
            interfaces = instance_info.get('network_interfaces', [])
            if interfaces and params['assign_public_ip']:
                nat = interfaces[0].get('primary_v4_address', {}).get('one_to_one_nat', {})
                instance_info['external_ip'] = nat.get('address')
        except Exception:
            pass
        return instance_info
    except subprocess.CalledProcessError as e:
        module.fail_json(msg=f"Failed to create VM: {e.stderr}")


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

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    result = dict(changed=False, instance_info=None)

    check_yc_cli(module)
    if module.params['service_account_key']:
        os.environ['YC_SERVICE_ACCOUNT_KEY_FILE'] = module.params['service_account_key']
    check_yc_auth(module, module.params['folder_id'])

    ssh_key_path = os.path.expanduser(module.params['ssh_key_path'])
    if not os.path.exists(ssh_key_path):
        module.fail_json(msg=f"SSH key not found: {ssh_key_path}")

    existing_vm = vm_exists(module, module.params['name'], module.params['folder_id'])

    if module.params['state'] == 'absent':
        if existing_vm:
            if not module.check_mode:
                delete_vm(module, module.params['name'], module.params['folder_id'])
                result['changed'] = True
                result['instance_info'] = {'status': 'deleted', 'name': module.params['name']}
            else:
                result['changed'] = True
        module.exit_json(**result)

    if existing_vm:
        result['instance_info'] = existing_vm
        result['changed'] = False
        module.exit_json(**result)

    if not module.check_mode:
        image_id = get_image_id(module, module.params['image_family'])
        instance_info = create_vm(module, module.params, image_id, ssh_key_path)
        result['instance_info'] = instance_info
        result['changed'] = True
    else:
        result['changed'] = True

    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()