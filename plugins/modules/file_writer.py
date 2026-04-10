#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, Aleksey Dubrovin
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r'''
---
module: file_writer
short_description: Creates a file with specified content
description:
  - This module creates a text file on a remote host with the specified content.
  - It is idempotent and will not change the file if the content already matches.
options:
  path:
    description:
      - Absolute path to the file to be created or modified.
    required: true
    type: str
  content:
    description:
      - Content to write into the file.
    required: true
    type: str
author:
  - Aleksey Dubrovin (@aleksey-dubrovin)
'''

EXAMPLES = r'''
- name: Create a configuration file
  aleksey_dubrovin.yandex_cloud_elk.file_writer:
    path: /etc/myapp/config.ini
    content: |
      [settings]
      debug=true
      log_level=INFO

- name: Create a temporary file
  aleksey_dubrovin.yandex_cloud_elk.file_writer:
    path: /tmp/test.txt
    content: "Hello from Ansible module"
'''

RETURN = r'''
path:
  description: Path to the file that was created or modified.
  type: str
  returned: always
  sample: "/tmp/test.txt"
content:
  description: Content that was written to the file.
  type: str
  returned: always
  sample: "Hello from Ansible module"
changed:
  description: Whether the file was created or modified.
  type: bool
  returned: always
  sample: true
'''

from ansible.module_utils.basic import AnsibleModule
import os

def run_module():
    module_args = dict(
        path=dict(type='str', required=True),
        content=dict(type='str', required=True)
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    path = module.params['path']
    content = module.params['content']

    result = dict(
        changed=False,
        path=path,
        content=content
    )

    # Проверяем, существует ли файл и совпадает ли содержимое
    file_exists = os.path.exists(path)
    content_matches = False

    if file_exists:
        try:
            with open(path, 'r') as f:
                if f.read() == content:
                    content_matches = True
        except Exception:
            pass

    # Если файла нет или содержимое отличается — требуется изменение
    if not file_exists or not content_matches:
        if not module.check_mode:
            # Создаём директорию, если нужно
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
            # Записываем содержимое
            with open(path, 'w') as f:
                f.write(content)
        result['changed'] = True

    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()