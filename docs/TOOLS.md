# Tool development and catalog

## Why tools are explicit

Desktop control is exposed as small, typed capabilities—not a generic command interpreter. A tool owns its argument/result schema, permission category, risk, confirmation requirement, timeout, validation, preview, and executor. Gemini receives the JSON declaration only when the tool is enabled.

The registry rejects duplicate names at startup and unknown calls at runtime. Execution validates the same arguments again, applies policy, enforces timeout/cancellation, validates the returned result model, redacts audit fields, and returns a structured success or failure.

## Included tools

Actual availability depends on Windows libraries, user settings, and configured allowlists.

### Low risk

| Tool | Capability | Important boundary |
| --- | --- | --- |
| `get_current_datetime` | Local date/time/timezone | No external access |
| `open_application` | Open an approved name/path or preferred alias | Aliases resolve only to absolute trusted `.exe` paths; no arguments or shell |
| `open_file_or_folder` | Open an existing literal path | Canonical path validation |
| `open_website` | Open default browser | Complete `http`/`https` URLs only; no embedded credentials |
| `search_local_files` | Bounded filename search | Configured roots, result/depth/time limits |
| `get_active_window` | Title/PID/process information | pywin32; reports unavailable on unsupported platforms |
| `list_running_applications` | Bounded process/application list | Structured metadata, no force action |
| `set_system_volume` | Set a validated 0–100 level | Native Core Audio scalar API with endpoint readback |
| `set_audio_muted` | Mute/unmute default output | Typed boolean |
| `read_clipboard` | Read text after permission policy | Contents redacted from logs/history |
| `set_clipboard` | Replace clipboard with bounded text | Contents redacted from logs/history |
| `take_screenshot` | Save a screenshot under an approved path | Permission controlled; no automatic cloud upload |
| `manage_window` | Focus/minimize/maximize/restore named window | Window-title/PID targeting; no screen coordinates |

“Low risk” does not mean unrestricted. Users can disable tools or set “ask every time,” and privacy-oriented categories such as clipboard/screen capture default conservatively.

### Medium risk

| Tool | Capability | Safeguard |
| --- | --- | --- |
| `type_text` | Append bounded plain text to the focused editable control | Confirmation/policy; no passwords or control characters; target-addressed UI Automation Value pattern with readback; terminals and shells rejected |
| `click_named_control` | Select a named tab/list item or allowlisted navigation control | Exact active-window binding, confirmation, no coordinates or form/transaction controls |
| `create_folder` | Create one folder | Canonical allowed path |
| `write_text_file` | Create/replace bounded plain text | Atomic no-replace create; handle-bound Windows overwrite/append; size limit |
| `move_or_rename_path` | Move one source to exact destination | Both paths previewed; Windows source handle and destination ancestors locked |
| `close_application` | Request graceful window/application close | No force kill |
| `run_approved_powershell_operation` | Invoke a named internal operation | Static operation registry and typed args only |
| `launch_development_command` | Run a configured command template | Developer mode, allowlist, trusted directory, confirmation, output/timeout cap |
| `execute_trusted_script` | Run a preconfigured script under trusted root | Exact extension/root, developer mode, confirmation |

### Developer execution configuration

Developer execution is disabled unless `developer_mode` is enabled in settings. Configure exact commands and scripts in `.env`; Gemini cannot create or extend these lists. JSON path values may use forward slashes on Windows, which avoids backslash escaping mistakes:

```dotenv
TRUSTED_SCRIPT_ROOTS_JSON=["C:/Users/you/JarvisScripts"]
TRUSTED_SCRIPT_ALLOWLIST_JSON=["C:/Users/you/JarvisScripts/build.ps1"]
TRUSTED_PYTHON_EXECUTABLE_PATH=C:/Python311/python.exe
DEVELOPMENT_COMMANDS_JSON={"frontend_tests":["C:/Program Files/nodejs/npm.cmd","run","test"]}
```

`execute_trusted_script` requires both an allowed root and an exact script entry. The backend records the approved file digest. At launch on Windows it opens that exact path with a read-only handle that denies write/delete sharing, rechecks the digest from the handle, and retains the handle until the interpreter exits. This closes the hash/reopen swap boundary. A verified private copy with cleanup provides the portable fallback. Python scripts additionally require the explicitly configured, trusted Python executable; they never inherit a model-selected interpreter. Keep trusted script roots separate from folders the assistant or other untrusted automation can modify.

Preferred application aliases are typed settings persisted in SQLite (or initially supplied through `PREFERRED_APPLICATIONS_JSON`). Each alias value must be an absolute `.exe` path. Resolution still requires the file to exist beneath `ProgramFiles`, `ProgramFiles(x86)`, or `SystemRoot`; aliases cannot add arguments, shell syntax, or a new trusted root.

`launch_development_command` accepts only a key from `DEVELOPMENT_COMMANDS_JSON`. Each value is a complete process argument array: the first item is the executable and the rest are fixed arguments. It is not passed through a command shell.

### High risk

| Tool | Capability | Safeguard |
| --- | --- | --- |
| `delete_path` | Delete an exact file/folder | Fresh confirmation; Windows handle-bound disposition; recursive reparse/race rejection |
| `force_terminate_process` | Kill a specific PID | Fresh confirmation, protected-process checks |
| `system_power_action` | Shutdown/restart/sleep/sign out | Exact action preview and fresh confirmation |
| `manage_software_package` | Named package install/uninstall where enabled | Valid package identifier, visible manager/action, fresh confirmation |

High-risk tools ignore “always allow.” The UI and backend both enforce this.

Broad email/message sending, form submission, credential entry, arbitrary elevated operations, and generic system-setting mutation are intentionally absent. Add application-specific tools with recipient/target previews instead of creating a broad automation escape hatch.

## PowerShell operations

The PowerShell runner is not a tool that accepts script text. It maps a small enum/name to an operation specification, such as directory/process/system information or a separately reviewed open/power implementation. Volume and mute use native Core Audio instead. A specification provides:

- Static executable/script body.
- Typed parameters.
- Parameter validator.
- Risk and confirmation flag.
- Timeout and output limit.
- Structured parser.

Identifiers reject shell metacharacters, encoded command syntax, line breaks, and suspicious token sequences. Arguments are passed as a process argument array wherever possible. Profiles are disabled and stdin is closed.

## Add a tool

Start with strict Pydantic models. The base API lives in `jarvis_assistant.tools.base`:

```python
from typing import ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field

from jarvis_assistant.cancellation import CancellationToken
from jarvis_assistant.models import PermissionCategory, RiskLevel
from jarvis_assistant.tools.base import BaseTool


class CreateNoteArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=20_000)


class CreateNoteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    path: str


class CreateNoteTool(BaseTool):
    name: ClassVar[str] = "create_note"
    description: ClassVar[str] = "Create one plain-text note in the configured notes folder."
    permission_category: ClassVar[PermissionCategory] = PermissionCategory.FILES
    risk_level: ClassVar[RiskLevel] = RiskLevel.MEDIUM
    confirmation_required: ClassVar[bool] = True
    timeout_seconds: ClassVar[float] = 8.0
    arguments_model: ClassVar[type[BaseModel]] = CreateNoteArguments
    result_model: ClassVar[type[BaseModel]] = CreateNoteResult

    def preview(self, arguments: BaseModel) -> str:
        values = cast(CreateNoteArguments, arguments)
        # Include the exact resolved path in real code.
        return f"Create the note {values.title!r} in the configured notes folder?"

    async def execute(
        self,
        arguments: BaseModel,
        cancellation: CancellationToken,
    ) -> CreateNoteResult:
        values = cast(CreateNoteArguments, arguments)
        cancellation.raise_if_cancelled()
        # Resolve through the shared safe-path service, use atomic file I/O,
        # then return only after the write is verified.
        ...
```

Then:

1. Inject required dependencies into the tool constructor. Do not read global mutable state from `execute`.
2. Register one instance in backend bootstrap. Duplicate names fail startup.
3. Add a default permission entry. Default to disabled/ask when privacy or mutation is involved.
4. Let the registry derive Gemini's declaration from `arguments_model.model_json_schema()`.
5. Add/update shared UI descriptions only if the backend descriptor does not already supply them.

## Tool test checklist

Every new tool should cover:

- Valid arguments and result model.
- Extra, missing, wrong-type, too-long, and suspicious values.
- Permission disabled.
- Ask/session/always behavior at its risk level.
- Forced high-risk confirmation, if applicable.
- Exact preview including all affected resources.
- Expired, denied, changed-digest, and replayed confirmation.
- Cancellation before and during execution.
- Timeout and subprocess termination.
- Missing target, access denied, elevated target, and platform unavailable.
- Structured success verified from actual state.
- Audit redaction for content-bearing fields.

## Risk guidance

- **Low:** observation or reversible user-session adjustment with limited privacy impact.
- **Medium:** writes data, sends input to an application, closes work, or runs a narrowly approved developer/system operation.
- **High:** destructive, forceful, external communication, install/uninstall, power/elevation/system mutation, credentials, or meaningful privacy exposure.

When uncertain, choose the higher level. Risk classification is a local security decision, never delegated to Gemini.

## UI Automation guidance

Prefer automation IDs, control type, and accessible name. Search within the intended application/window and bound traversal/time. The generic tool is deliberately limited to tab/list selection and a small allowlist of non-committing navigation controls; messages, forms, credentials, purchases, and destructive actions require purpose-built high-risk tools whose previews can disclose the real effect. Invoke the control's UIA pattern instead of synthesizing a mouse click. If the target is elevated, report the Windows integrity-level restriction rather than retrying as administrator.

## Avoiding false success

A launched process handle does not always mean an application finished opening; a posted window message does not prove work was saved; an OS request can be rejected. Define success at the narrow level actually observed and phrase the result accordingly. For example, return “Requested that Notepad close” unless window disappearance was verified.
