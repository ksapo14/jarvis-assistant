use std::{
    env, fs, io,
    path::PathBuf,
    process::{Child, Command},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::{Duration, Instant},
};

use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, RunEvent,
};
use tauri_plugin_autostart::MacosLauncher;

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendConfig {
    session_token: String,
    base_url: String,
}

struct AppRuntime {
    session_token: String,
    configured_port: Option<u16>,
    backend_port: Mutex<Option<u16>>,
    backend: Mutex<Option<Child>>,
    shutting_down: AtomicBool,
}

impl AppRuntime {
    fn new() -> Result<Self, String> {
        Ok(Self {
            session_token: session_token()?,
            configured_port: configured_backend_port()?,
            backend_port: Mutex::new(None),
            backend: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
        })
    }
}

struct UuidToken;

impl UuidToken {
    fn new() -> String {
        uuid::Uuid::new_v4().simple().to_string()
    }
}

#[tauri::command]
fn get_backend_config(runtime: tauri::State<'_, AppRuntime>) -> Result<BackendConfig, String> {
    let port = runtime
        .backend_port
        .lock()
        .map_err(|_| "backend connection lock poisoned".to_string())?
        .ok_or_else(|| "Assistant backend is not ready yet".to_string())?;
    Ok(BackendConfig {
        session_token: runtime.session_token.clone(),
        base_url: backend_base_url(port),
    })
}

#[tauri::command]
fn open_log_directory() -> Result<(), String> {
    let data_dir = match env::var_os("ASSISTANT_DATA_DIR").filter(|value| !value.is_empty()) {
        Some(configured) => PathBuf::from(configured),
        None => env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join("JarvisAssistant"),
    };
    let log_dir = data_dir.join("logs");
    std::fs::create_dir_all(&log_dir)
        .map_err(|error| format!("Could not create the log directory: {error}"))?;
    Command::new("explorer.exe")
        .arg(&log_dir)
        .spawn()
        .map_err(|error| format!("Could not open the log directory: {error}"))?;
    Ok(())
}

fn show_window(app: &tauri::AppHandle, destination: Option<&str>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        if let Some(page) = destination {
            let _ = app.emit("navigate", page);
        }
    }
}

fn configured_backend_port() -> Result<Option<u16>, String> {
    match env::var("ASSISTANT_PORT") {
        Ok(configured) if !configured.trim().is_empty() => {
            let port = configured
                .parse::<u16>()
                .map_err(|_| "ASSISTANT_PORT must be a valid TCP port".to_string())?;
            if 0 < port && port < 1024 {
                return Err("ASSISTANT_PORT must be 0 or at least 1024".to_string());
            }
            Ok(Some(port))
        }
        _ => Ok(None),
    }
}

fn session_token() -> Result<String, String> {
    if let Ok(configured) = env::var("ASSISTANT_SESSION_TOKEN") {
        if configured.len() < 32 {
            return Err("ASSISTANT_SESSION_TOKEN must contain at least 32 characters".to_string());
        }
        return Ok(configured);
    }
    Ok(format!("{}{}", UuidToken::new(), UuidToken::new()))
}

fn backend_base_url(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

fn backend_url(port: u16, path: &str) -> String {
    format!("{}{path}", backend_base_url(port))
}

fn send_backend(
    app: &tauri::AppHandle,
    method: reqwest::Method,
    path: &'static str,
    body: serde_json::Value,
) {
    let runtime = app.state::<AppRuntime>();
    let token = runtime.session_token.clone();
    let port = match runtime.backend_port.lock() {
        Ok(guard) => match *guard {
            Some(port) => port,
            None => return,
        },
        Err(_) => return,
    };
    std::thread::spawn(move || {
        let client = match reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(4))
            .build()
        {
            Ok(client) => client,
            Err(_) => return,
        };
        let _ = client
            .request(method, backend_url(port, path))
            .header("X-Assistant-Token", token)
            .json(&body)
            .send();
    });
}

fn tray_icon() -> Image<'static> {
    const SIZE: usize = 32;
    let mut rgba = vec![0_u8; SIZE * SIZE * 4];
    let center = (SIZE as f32 - 1.0) / 2.0;
    for y in 0..SIZE {
        for x in 0..SIZE {
            let dx = x as f32 - center;
            let dy = y as f32 - center;
            let distance = (dx * dx + dy * dy).sqrt();
            let ring = (9.0..=13.0).contains(&distance);
            let core = distance <= 3.2;
            if ring || core {
                let index = (y * SIZE + x) * 4;
                rgba[index] = 125;
                rgba[index + 1] = 223;
                rgba[index + 2] = 199;
                rgba[index + 3] = 255;
            }
        }
    }
    Image::new_owned(rgba, SIZE as u32, SIZE as u32)
}

fn configure_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open assistant", true, None::<&str>)?;
    let listen = MenuItem::with_id(app, "listen", "Start listening", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pause wake word", true, None::<&str>)?;
    let resume = MenuItem::with_id(app, "resume", "Resume wake word", true, None::<&str>)?;
    let mute = MenuItem::with_id(app, "mute", "Mute voice responses", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
    let activity = MenuItem::with_id(app, "activity", "View recent activity", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let separator_one = PredefinedMenuItem::separator(app)?;
    let separator_two = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &open,
            &listen,
            &separator_one,
            &pause,
            &resume,
            &mute,
            &separator_two,
            &settings,
            &activity,
            &quit,
        ],
    )?;

    TrayIconBuilder::with_id("jarvis-tray")
        .icon(tray_icon())
        .tooltip("JARVIS · Local desktop assistant")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_window(app, Some("assistant")),
            "listen" => {
                show_window(app, Some("assistant"));
                send_backend(
                    app,
                    reqwest::Method::POST,
                    "/v1/listen/start",
                    serde_json::json!({}),
                );
            }
            "pause" => send_backend(
                app,
                reqwest::Method::PATCH,
                "/v1/settings",
                serde_json::json!({ "wake_word_enabled": false }),
            ),
            "resume" => send_backend(
                app,
                reqwest::Method::PATCH,
                "/v1/settings",
                serde_json::json!({ "wake_word_enabled": true }),
            ),
            "mute" => send_backend(
                app,
                reqwest::Method::POST,
                "/v1/voice/mute",
                serde_json::json!({ "muted": true }),
            ),
            "settings" => show_window(app, Some("general")),
            "activity" => show_window(app, Some("history")),
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_window(tray.app_handle(), Some("assistant"));
            }
        })
        .build(app)?;
    Ok(())
}

fn find_backend() -> (PathBuf, Vec<String>) {
    if let Ok(configured) = env::var("JARVIS_BACKEND_EXECUTABLE") {
        return (PathBuf::from(configured), Vec::new());
    }
    if let Ok(current) = env::current_exe() {
        let sibling = current.with_file_name("jarvis-assistant.exe");
        if sibling.is_file() {
            return (sibling, Vec::new());
        }
    }
    let development = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
        .join(".venv")
        .join("Scripts")
        .join("jarvis-assistant.exe");
    if development.is_file() {
        return (development, Vec::new());
    }
    (
        PathBuf::from("python"),
        vec!["-m".into(), "jarvis_assistant".into()],
    )
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct BackendReadiness {
    nonce: String,
    port: u16,
    pid: u32,
    parent_pid: u32,
}

struct ReadinessFile(PathBuf);

impl Drop for ReadinessFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn readiness_file(nonce: &str) -> Result<ReadinessFile, io::Error> {
    let root = env::temp_dir();
    let root = if root.is_absolute() {
        root
    } else {
        env::current_dir()?.join(root)
    };
    let path = root.join(format!(
        "jarvis-assistant-ready-{}-{nonce}.json",
        std::process::id()
    ));
    match fs::remove_file(&path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    Ok(ReadinessFile(path))
}

fn parse_readiness(contents: &str, expected_nonce: &str, launcher_pid: u32) -> Result<u16, String> {
    let readiness: BackendReadiness = serde_json::from_str(contents)
        .map_err(|_| "invalid backend readiness response".to_string())?;
    if readiness.nonce != expected_nonce {
        return Err("backend readiness nonce did not match".to_string());
    }
    // Development runs Python directly, while a PyInstaller one-file executable
    // starts its Python runtime as a child of the spawned bootloader. Accept only
    // those two process shapes so a valid nonce cannot redirect the host to an
    // unrelated local process.
    if readiness.pid == 0 || (readiness.pid != launcher_pid && readiness.parent_pid != launcher_pid)
    {
        return Err("backend readiness process lineage did not match".to_string());
    }
    if readiness.port < 1024 {
        return Err("backend reported an invalid loopback port".to_string());
    }
    Ok(readiness.port)
}

fn authenticated_health_check(client: &reqwest::blocking::Client, port: u16, token: &str) -> bool {
    client
        .get(backend_url(port, "/v1/health"))
        .header("X-Assistant-Token", token)
        .send()
        .is_ok_and(|response| response.status().is_success())
}

fn wait_for_managed_readiness(
    child: &mut Child,
    path: &PathBuf,
    nonce: &str,
    token: &str,
) -> Result<u16, String> {
    let launcher_pid = child.id();
    let deadline = Instant::now() + Duration::from_secs(20);
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(750))
        .build()
        .map_err(|error| format!("could not create backend readiness client: {error}"))?;
    let mut reported_port = None;

    while Instant::now() < deadline {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("could not inspect backend process: {error}"))?
        {
            return Err(format!(
                "assistant backend exited before readiness with status {status}"
            ));
        }
        if reported_port.is_none() && path.is_file() {
            let metadata = fs::metadata(path)
                .map_err(|error| format!("could not inspect backend readiness file: {error}"))?;
            if metadata.len() > 4096 {
                return Err("backend readiness response was too large".to_string());
            }
            let contents = fs::read_to_string(path)
                .map_err(|error| format!("could not read backend readiness file: {error}"))?;
            reported_port = Some(parse_readiness(&contents, nonce, launcher_pid)?);
        }
        if let Some(port) = reported_port {
            if authenticated_health_check(&client, port, token) {
                return Ok(port);
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Err("assistant backend did not become ready within 20 seconds".to_string())
}

fn wait_for_unmanaged_backend(port: u16, token: &str) -> Result<(), String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(750))
        .build()
        .map_err(|error| format!("could not create backend readiness client: {error}"))?;
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if authenticated_health_check(&client, port, token) {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("the externally managed assistant backend is not ready".to_string())
}

fn start_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let runtime = app.state::<AppRuntime>();
    *runtime
        .backend_port
        .lock()
        .map_err(|_| io::Error::other("backend connection lock poisoned"))? = None;

    if env::var("ASSISTANT_BACKEND_MANAGED").as_deref() == Ok("0") {
        let port = runtime.configured_port.unwrap_or(8765);
        if port == 0 {
            return Err(io::Error::other(
                "ASSISTANT_PORT=0 cannot identify an externally managed backend",
            )
            .into());
        }
        wait_for_unmanaged_backend(port, &runtime.session_token).map_err(io::Error::other)?;
        *runtime
            .backend_port
            .lock()
            .map_err(|_| io::Error::other("backend connection lock poisoned"))? = Some(port);
        return Ok(());
    }

    let (executable, arguments) = find_backend();
    let nonce = format!("{}{}", UuidToken::new(), UuidToken::new());
    let ready = readiness_file(&nonce)?;
    let mut command = Command::new(executable);
    command
        .args(arguments)
        .env("ASSISTANT_SESSION_TOKEN", &runtime.session_token)
        .env("ASSISTANT_PARENT_PID", std::process::id().to_string())
        .env("ASSISTANT_HOST", "127.0.0.1")
        .env(
            "ASSISTANT_PORT",
            runtime.configured_port.unwrap_or(0).to_string(),
        )
        .env("ASSISTANT_READY_FILE", &ready.0)
        .env("ASSISTANT_READY_NONCE", &nonce)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command.spawn()?;
    let port =
        match wait_for_managed_readiness(&mut child, &ready.0, &nonce, &runtime.session_token) {
            Ok(port) => port,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(io::Error::other(error).into());
            }
        };

    match (runtime.backend_port.lock(), runtime.backend.lock()) {
        (Ok(mut port_guard), Ok(mut backend_guard)) => {
            *backend_guard = Some(child);
            *port_guard = Some(port);
        }
        _ => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(io::Error::other("backend runtime lock poisoned").into());
        }
    }
    Ok(())
}

fn stop_backend(app: &tauri::AppHandle) {
    let runtime = app.state::<AppRuntime>();
    if runtime.shutting_down.swap(true, Ordering::AcqRel) {
        return;
    }
    let port = runtime
        .backend_port
        .lock()
        .ok()
        .and_then(|mut guard| guard.take());
    if env::var("ASSISTANT_BACKEND_MANAGED").as_deref() == Ok("0") {
        return;
    }
    let token = runtime.session_token.clone();
    if let Some(port) = port {
        if let Ok(client) = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()
        {
            let _ = client
                .post(backend_url(port, "/v1/shutdown"))
                .header("X-Assistant-Token", token)
                .json(&serde_json::json!({}))
                .send();
        }
    }
    if let Ok(mut guard) = runtime.backend.lock() {
        if let Some(child) = guard.as_mut() {
            let deadline = Instant::now() + Duration::from_secs(4);
            while Instant::now() < deadline {
                match child.try_wait() {
                    Ok(Some(_)) => {
                        *guard = None;
                        return;
                    }
                    Ok(None) => std::thread::sleep(Duration::from_millis(100)),
                    Err(_) => break,
                }
            }
            let _ = child.kill();
            let _ = child.wait();
        }
        *guard = None;
    };
}

fn monitor_backend(app: &tauri::AppHandle) {
    if env::var("ASSISTANT_BACKEND_MANAGED").as_deref() == Ok("0") {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(2));
        let runtime = app.state::<AppRuntime>();
        if runtime.shutting_down.load(Ordering::Acquire) {
            return;
        }
        let should_restart = match runtime.backend.lock() {
            Ok(mut guard) => match guard.as_mut() {
                Some(child) => match child.try_wait() {
                    Ok(Some(_)) => {
                        *guard = None;
                        true
                    }
                    Ok(None) => false,
                    Err(_) => return,
                },
                None => true,
            },
            Err(_) => return,
        };
        if should_restart && !runtime.shutting_down.load(Ordering::Acquire) {
            let was_connected = runtime
                .backend_port
                .lock()
                .ok()
                .and_then(|mut guard| guard.take())
                .is_some();
            if was_connected {
                let _ = app.emit("backend-disconnected", ());
            }
            std::thread::sleep(Duration::from_secs(1));
            if start_backend(&app).is_ok() {
                let _ = app.emit("backend-restarted", ());
            }
        }
    });
}

pub fn run() {
    let runtime = AppRuntime::new().expect("failed to configure the backend connection");
    let app = tauri::Builder::default()
        .manage(runtime)
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_window(app, Some("assistant"));
        }))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--minimized"]),
        ))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            get_backend_config,
            open_log_directory
        ])
        .setup(|app| {
            configure_tray(app)?;
            start_backend(app.handle())?;
            monitor_backend(app.handle());
            if env::args().any(|arg| arg == "--minimized") {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the JARVIS desktop host");

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(app);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{parse_readiness, wait_for_managed_readiness};
    use std::{env, fs, process::Command, thread, time::Duration};

    #[test]
    fn readiness_requires_matching_nonce_runtime_pid_and_safe_port() {
        let nonce = "a".repeat(64);
        let direct = format!(r#"{{"nonce":"{nonce}","port":49152,"pid":42,"parent_pid":7}}"#);
        assert_eq!(parse_readiness(&direct, &nonce, 42).unwrap(), 49152);
        assert!(parse_readiness(&direct, &"b".repeat(64), 42).is_err());

        let frozen = format!(r#"{{"nonce":"{nonce}","port":49152,"pid":99,"parent_pid":42}}"#);
        assert_eq!(parse_readiness(&frozen, &nonce, 42).unwrap(), 49152);

        let unrelated = format!(r#"{{"nonce":"{nonce}","port":49152,"pid":99,"parent_pid":98}}"#);
        assert!(parse_readiness(&unrelated, &nonce, 42).is_err());

        let zero_pid = format!(r#"{{"nonce":"{nonce}","port":49152,"pid":0,"parent_pid":42}}"#);
        assert!(parse_readiness(&zero_pid, &nonce, 42).is_err());

        let low_port = format!(r#"{{"nonce":"{nonce}","port":80,"pid":42,"parent_pid":7}}"#);
        assert!(parse_readiness(&low_port, &nonce, 42).is_err());
    }

    #[test]
    fn readiness_schema_rejects_extra_fields() {
        let nonce = "a".repeat(64);
        let payload = format!(
            r#"{{"nonce":"{nonce}","port":49152,"pid":42,"parent_pid":7,"session_token":"leak"}}"#
        );
        assert!(parse_readiness(&payload, &nonce, 42).is_err());
    }

    #[test]
    #[ignore = "requires the freshly built PyInstaller executable"]
    fn packaged_backend_handshake_matches_host_contract() {
        let executable = env::var_os("JARVIS_PACKAGED_BACKEND_TEST_PATH")
            .expect("JARVIS_PACKAGED_BACKEND_TEST_PATH must identify the packaged backend");
        let nonce = "n".repeat(64);
        let token = "t".repeat(64);
        let root = env::temp_dir().join(format!("jarvis-rust-smoke-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).expect("could not create packaged-backend smoke directory");
        let ready_file = root.join("ready.json");

        let mut child = Command::new(executable)
            .env("ASSISTANT_ENV", "mock")
            .env("ASSISTANT_SESSION_TOKEN", &token)
            .env("ASSISTANT_HOST", "127.0.0.1")
            .env("ASSISTANT_PORT", "0")
            .env("ASSISTANT_DATA_DIR", &root)
            .env("ASSISTANT_READY_FILE", &ready_file)
            .env("ASSISTANT_READY_NONCE", &nonce)
            .env("ASSISTANT_PARENT_PID", std::process::id().to_string())
            .spawn()
            .expect("could not launch the packaged backend");

        let result = (|| {
            let port = wait_for_managed_readiness(&mut child, &ready_file, &nonce, &token)?;
            let client = reqwest::blocking::Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .map_err(|error| error.to_string())?;
            let response = client
                .post(format!("http://127.0.0.1:{port}/v1/shutdown"))
                .header("X-Assistant-Token", &token)
                .json(&serde_json::json!({}))
                .send()
                .map_err(|error| error.to_string())?;
            if !response.status().is_success() {
                return Err(format!("shutdown returned {}", response.status()));
            }

            for _ in 0..200 {
                if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
                    if status.success() {
                        return Ok(());
                    }
                    return Err(format!("packaged backend exited with {status}"));
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err("packaged backend did not exit after shutdown".to_string())
        })();

        if child.try_wait().ok().flatten().is_none() {
            let _ = child.kill();
            let _ = child.wait();
        }
        let _ = fs::remove_dir_all(&root);
        result.expect("packaged backend must satisfy the Rust host handshake");
    }
}
