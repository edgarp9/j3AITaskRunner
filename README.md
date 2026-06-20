# j3AITaskRunner

A Windows desktop task runner for managing workspace-based AI CLI sessions and queued agent jobs.

j3AITaskRunner is a Python/Tkinter application for coordinating AI coding workflows across multiple local workspaces. Each workspace has its own session tabs and task queue, so different projects can run independently while keeping prompts, progress logs, and final responses separated.

This project was made with AI assistance as an in-house tool. It is useful for the author's workflow, but it is not a polished general-purpose product. Automated test coverage and real-world provider verification are still limited, so validate behavior in your own environment before relying on it.

<img width="465" height="283" alt="j3AITaskRunner" src="https://github.com/user-attachments/assets/b0e204da-85bd-454e-90e6-ab5bdd712ad2" />


## Features

- Manage multiple workspaces from one desktop window.
- Create multiple session tabs per workspace.
- Queue prompts and run one task at a time per workspace.
- Run queues independently across different workspaces.
- Configure external AI CLI providers: Codex CLI, Claude Code, Kilo Code, OpenCode, and Pi Coding Agent. Only Codex CLI has been tested. 
- Keep live progress logs separate from completed prompt/response history.
- Optionally save execution artifacts and provider logs for debugging.
- Use bundled preset prompt templates to generate candidate work sessions.
- Store settings and workspace lists locally in a JSON file.
- Switch the UI between English and Korean.

## Requirements

- Windows
- Python with Tkinter available
- At least one supported AI CLI installed and authenticated if you want to run real tasks
- Optional: `tkinterdnd2` for workspace drag-and-drop support

The application can still start without `tkinterdnd2`; drag-and-drop is simply unavailable.

## Run From Source

```powershell
py src\main.py
```

Optional drag-and-drop dependency:

```powershell
py -m pip install tkinterdnd2
```

Open the settings dialog in the app and configure the executable path for the AI runner you want to use. The path can be an executable, a directory containing the executable, or a command name available on `PATH`.

## Build A Release

```powershell
py src\build_release.py
```

The build script creates a managed build virtual environment, installs PyInstaller and `tkinterdnd2`, and writes the release bundle under:

```text
src\dist\windows\j3AITaskRunner\
```

## Provider Verification

Only Codex CLI has been tested by the author so far. Claude Code, Kilo Code, OpenCode, and Pi Coding Agent support is implemented through provider adapters, but those providers have not been fully verified in real use.

## Preset Prompt Customization

Release builds load preset prompts from:

```text
j3AITaskRunner\lib\prompt
```

The bundled prompt files in `j3AITaskRunner\lib\prompt` are written in Korean. You can edit them in Korean or replace them with prompts in another language, as long as the file naming and placeholder rules below are preserved.

The folder is organized by language:

```text
j3AITaskRunner\lib\prompt\Python\
j3AITaskRunner\lib\prompt\Rust\
j3AITaskRunner\lib\prompt\Kotlin\
```

Each preset instruction must have two Markdown files with the same base name:

```text
bug.md
bug_work.md
```

The first file, such as `bug.md`, is the analysis prompt used by a preset session. It should ask the AI runner to analyze the workspace and return candidate work items.

The second file, such as `bug_work.md`, is the work-prompt generation template used after candidates are selected. It must include this placeholder:

```text
{{candidates_payload}}
```

To edit or add a preset:

1. Close j3AITaskRunner.
2. Open `j3AITaskRunner\lib\prompt`.
3. Choose or create a language folder.
4. Edit an existing `instruction.md` and matching `instruction_work.md`, or add a new pair with the same instruction name.
5. Keep the `_work.md` file's `{{candidates_payload}}` placeholder.
6. Start j3AITaskRunner again.
7. Create a preset session and select the matching Language and Instruction values.

Prompt files are cached while the app is running, so restart the app after editing files in `j3AITaskRunner\lib\prompt`.

## Tests

```powershell
cd src
py -m unittest discover -s tests
```

Most tests use fake executables or mocked process contracts. They are helpful for checking core behavior, but they do not fully prove compatibility with every installed AI CLI version. Real provider smoke testing is intentionally limited and should be repeated in the target environment. At this stage, only Codex CLI has been tested by the author.

## Project Status

j3AITaskRunner is early-stage internal tooling. Provider CLI contracts can change, especially for non-interactive JSON or stream modes. Review logs and artifacts when upgrading any external AI CLI.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).

## Icon Notice And Thanks

This project uses icons from [Google Fonts Icons](https://fonts.google.com/icons), including Material Symbols / Material Icons assets provided by Google under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

Thank you to Google and the Material Symbols / Material Icons contributors for making these icons available.
