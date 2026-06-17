This project uses pixi for development dependency management and conda for runtime. Do not use pip or uv for dependencies.

- Dependencies are declared in `pixi.toml` (single source of truth)
- `pixi install` installs all conda deps (run `just deps`)
- `just install` builds & installs the project into the pixi environment
- The shipped `bioconda_utils/bioconda_utils-requirements.txt` is auto-generated from `pixi.toml` by `scripts/generate-requirements-txt.py`
- After changing deps in `pixi.toml`, run `just regenerate-requirements` and commit the updated `.txt`

Do not run the full test suite unless absolutely, the long running tests take about an hour to complete. 
Running cheap tests frequently is recommended.
Occasionally run `just check` to verify your work.

Read ./justfile for commands frequently useful during development.
