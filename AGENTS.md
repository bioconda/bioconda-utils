This project uses pixi for development dependency management and conda for runtime. Do not use pip or uv to manage dependencies.

- Dependencies are declared in `pixi.toml` (single source of truth)
- Pixi automatically installs the required environment when running a task
- `just install` builds the project in the pixi environment and symlinks the CLI into `~/.local/bin`

Do not run the full test suite unless absolutely necessary, the long running tests take about an hour to complete. 
Run `just format` frequently and `just check` after a moderate amount of changes.

Read `./justfile` for commands frequently useful during development. These
commands are thin wrappers around tasks declared in `pixi.toml`.
