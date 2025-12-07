Manifests define safe, whitelisted host capabilities.

Example:

{
  "name": "read_file",
  "allowed_args": ["path"],
  "allowed_paths": ["/home/user/data/"],
  "exec": {
    "cmd": "cat {path}",
    "shell": false,
    "timeout": 5
  }
}

Only what is declared here is allowed.
Edit these files carefully — they are your OS-level policy boundaries.
