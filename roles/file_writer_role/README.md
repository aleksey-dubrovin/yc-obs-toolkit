# file_writer_role

Роль-обёртка для модуля `file_writer`, создающего файлы с содержимым.

## Переменные

| Имя | Значение по умолчанию | Описание |
|-----|----------------------|----------|
| `file_writer_path` | `/tmp/default_file.txt` | Путь к создаваемому файлу |
| `file_writer_content` | `"Default content from file_writer_role"` | Содержимое файла |

## Пример использования

```yaml
- hosts: all
  roles:
    - role: file_writer_role
      vars:
        file_writer_path: /opt/app/config.txt
        file_writer_content: "custom configuration"