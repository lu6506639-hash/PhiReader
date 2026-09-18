#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::Manager;

#[cfg(windows)]
fn hide_child_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    // Keep parser helpers from opening a transient console window. The
    // helpers are console applications (both the Python interpreter and the
    // bundled PyInstaller executables), so CREATE_NO_WINDOW is the Windows
    // process flag designed for this case.
    command.creation_flags(0x08000000);
}

#[cfg(not(windows))]
fn hide_child_window(_command: &mut Command) {}

static FOLDER_STORE_LOCK: Mutex<()> = Mutex::new(());
const API_KEY_SERVICE: &str = "com.phireader.desktop";
const QWEN_CHAT_ENDPOINT: &str =
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions";
const DEEPSEEK_CHAT_ENDPOINT: &str = "https://api.deepseek.com/chat/completions";

fn valid_api_key_provider(provider: &str) -> bool {
    matches!(provider, "qwen" | "deepseek" | "custom")
}

fn api_key_entry(provider: &str) -> Result<keyring::Entry, String> {
    if !valid_api_key_provider(provider) {
        return Err("不支持的 API Key 服务商".to_string());
    }
    keyring::Entry::new(API_KEY_SERVICE, provider).map_err(|error| error.to_string())
}

#[tauri::command]
fn get_api_key_status() -> Result<HashMap<String, bool>, String> {
    let mut status = HashMap::new();
    for provider in ["qwen", "deepseek", "custom"] {
        let entry = api_key_entry(provider)?;
        match entry.get_password() {
            Ok(api_key) => {
                status.insert(provider.to_string(), !api_key.trim().is_empty());
            }
            Err(keyring::Error::NoEntry) => {
                status.insert(provider.to_string(), false);
            }
            Err(error) => return Err(format!("无法读取系统凭据库：{error}")),
        }
    }
    Ok(status)
}

#[tauri::command]
fn set_api_key(provider: String, api_key: String) -> Result<(), String> {
    let entry = api_key_entry(&provider)?;
    let api_key = api_key.trim();
    if api_key.is_empty() {
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(format!("无法从系统凭据库删除 API Key：{error}")),
        }
    } else {
        entry
            .set_password(&api_key)
            .map_err(|error| format!("无法保存到系统凭据库：{error}"))?;
        let verified = entry
            .get_password()
            .map_err(|error| format!("无法验证系统凭据库中的 API Key：{error}"))?;
        if verified != api_key {
            return Err("系统凭据库未能验证刚保存的 API Key".to_string());
        }
        Ok(())
    }
}

fn model_endpoint(provider: &str, custom_endpoint: Option<&str>) -> Result<reqwest::Url, String> {
    let endpoint = match provider {
        "qwen" => QWEN_CHAT_ENDPOINT,
        "deepseek" => DEEPSEEK_CHAT_ENDPOINT,
        "custom" => custom_endpoint.ok_or("未配置自定义 API 地址")?,
        _ => return Err("不支持的 API Key 服务商".to_string()),
    };
    let url = reqwest::Url::parse(endpoint).map_err(|_| "API 地址无效".to_string())?;
    if !url.username().is_empty() || url.password().is_some() {
        return Err("API 地址不能包含用户名或密码".to_string());
    }
    let is_loopback = url.host_str().is_some_and(|host| {
        host.eq_ignore_ascii_case("localhost")
            || host
                .parse::<std::net::IpAddr>()
                .is_ok_and(|address| address.is_loopback())
    });
    if url.scheme() != "https" && !(url.scheme() == "http" && is_loopback) {
        return Err("API 地址必须使用 HTTPS（本机回环地址除外）".to_string());
    }
    Ok(url)
}

#[tauri::command]
async fn request_model_completion(
    provider: String,
    custom_endpoint: Option<String>,
    body: Value,
) -> Result<String, String> {
    let url = model_endpoint(&provider, custom_endpoint.as_deref())?;
    let api_key = api_key_entry(&provider)?
        .get_password()
        .map_err(|error| match error {
            keyring::Error::NoEntry => "请先在设置中配置 API Key".to_string(),
            _ => format!("无法读取系统凭据库：{error}"),
        })?;
    if api_key.trim().is_empty() {
        return Err("请先在设置中配置 API Key".to_string());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(90))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|error| format!("无法初始化模型请求：{error}"))?;
    let response = client
        .post(url)
        .bearer_auth(api_key)
        .json(&body)
        .send()
        .await
        .map_err(|error| {
            if error.is_timeout() {
                "请求超时（90 秒）".to_string()
            } else {
                format!("模型请求失败：{error}")
            }
        })?;
    let status = response.status();
    let response_body = response
        .text()
        .await
        .map_err(|error| format!("无法读取模型响应：{error}"))?;
    if !status.is_success() {
        let detail: String = response_body
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .chars()
            .take(180)
            .collect();
        return Err(format!(
            "模型 API {}{}",
            status.as_u16(),
            if detail.is_empty() {
                String::new()
            } else {
                format!("：{detail}")
            }
        ));
    }
    Ok(response_body)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ParseResponse {
    paper_id: String,
    paper_path: String,
    parse_path: String,
    metadata: Value,
    symbols: Value,
    totals: Value,
    llm_used: bool,
    visual_review_queue: Vec<Value>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RenderResponse {
    image_data: String,
    page_width: f64,
    page_height: f64,
    occurrences: Vec<Value>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct StoredPaper {
    paper_id: String,
    paper_path: String,
    parse_path: String,
    title: String,
    metadata: Value,
    symbols: Value,
    totals: Value,
    visual_review_queue: Vec<Value>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PaperFolder {
    id: String,
    name: String,
    #[serde(default)]
    paper_ids: Vec<String>,
}

#[derive(Clone, Default, Deserialize, Serialize)]
struct FolderStore {
    #[serde(default)]
    folders: Vec<PaperFolder>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BatchDeleteFailure {
    paper_id: String,
    error: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BatchDeleteResult {
    deleted_ids: Vec<String>,
    failures: Vec<BatchDeleteFailure>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SymbolMeaningUpdate {
    id: String,
    meaning: String,
}

fn encode_base64(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let first = chunk[0] as u32;
        let second = chunk.get(1).copied().unwrap_or(0) as u32;
        let third = chunk.get(2).copied().unwrap_or(0) as u32;
        let value = (first << 16) | (second << 8) | third;
        output.push(TABLE[((value >> 18) & 63) as usize] as char);
        output.push(TABLE[((value >> 12) & 63) as usize] as char);
        output.push(if chunk.len() > 1 {
            TABLE[((value >> 6) & 63) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            TABLE[(value & 63) as usize] as char
        } else {
            '='
        });
    }
    output
}

fn tool_path(app: &tauri::AppHandle, file_name: &str) -> Result<(PathBuf, bool), String> {
    let current = std::env::current_dir().map_err(|error| error.to_string())?;
    let helper_name = match file_name {
        "extract.py" => "phireader-extract.exe",
        "render_page.py" => "phireader-render.exe",
        _ => return Err(format!("未知解析工具：{file_name}")),
    };
    let mut candidates = Vec::new();
    if let Ok(resources) = app.path().resource_dir() {
        candidates.push((
            resources.join("parser").join("release").join(helper_name),
            true,
        ));
        candidates.push((resources.join("parser").join(file_name), false));
    }
    candidates.push((
        current.join("parser").join("release").join(helper_name),
        true,
    ));
    candidates.push((current.join("parser").join(file_name), false));
    candidates.push((current.join("..").join("parser").join(file_name), false));
    candidates
        .into_iter()
        .find(|(path, _)| path.is_file())
        .ok_or_else(|| format!("找不到 parser/{file_name}；安装资源可能不完整"))
}

fn parser_tool(app: &tauri::AppHandle) -> Result<(PathBuf, bool), String> {
    tool_path(app, "extract.py")
}

fn render_tool(app: &tauri::AppHandle) -> Result<(PathBuf, bool), String> {
    tool_path(app, "render_page.py")
}

fn papers_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|path| path.join("papers"))
        .map_err(|error| format!("无法定位应用数据目录：{error}"))
}

fn folders_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|path| path.join("folders.json"))
        .map_err(|error| format!("无法定位应用数据目录：{error}"))
}

fn read_folder_store(path: &Path) -> Result<FolderStore, String> {
    if !path.exists() {
        return Ok(FolderStore::default());
    }
    let text = fs::read_to_string(path).map_err(|error| format!("无法读取目录数据：{error}"))?;
    serde_json::from_str(&text).map_err(|error| format!("目录数据无效：{error}"))
}

fn write_folder_store(path: &Path, store: &FolderStore) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("无法创建应用数据目录：{error}"))?;
    }
    let bytes =
        serde_json::to_vec_pretty(store).map_err(|error| format!("无法生成目录数据：{error}"))?;
    fs::write(path, bytes).map_err(|error| format!("无法保存目录数据：{error}"))
}

fn valid_folder_name(name: &str) -> bool {
    !name.is_empty() && name.chars().count() <= 80 && !name.chars().any(char::is_control)
}

fn remove_paper_references(store: &mut FolderStore, paper_id: &str) {
    for folder in &mut store.folders {
        folder.paper_ids.retain(|id| id != paper_id);
    }
}

fn remove_folder(store: &mut FolderStore, folder_id: &str) -> bool {
    let original_len = store.folders.len();
    store.folders.retain(|folder| folder.id != folder_id);
    store.folders.len() != original_len
}

fn remove_paper_reference(
    store: &mut FolderStore,
    folder_id: &str,
    paper_id: &str,
) -> Result<(), &'static str> {
    let folder = store
        .folders
        .iter_mut()
        .find(|folder| folder.id == folder_id)
        .ok_or("目录不存在")?;
    if !folder.paper_ids.iter().any(|id| id == paper_id) {
        return Err("论文不在该目录中");
    }
    folder.paper_ids.retain(|id| id != paper_id);
    Ok(())
}

fn remove_paper_references_from_folder(
    store: &mut FolderStore,
    folder_id: &str,
    paper_ids: &[String],
) -> Result<(), &'static str> {
    let selected: HashSet<&str> = paper_ids.iter().map(String::as_str).collect();
    if selected.len() != paper_ids.len() {
        return Err("论文选择包含重复项");
    }
    let folder = store
        .folders
        .iter_mut()
        .find(|folder| folder.id == folder_id)
        .ok_or("目录不存在")?;
    if selected
        .iter()
        .any(|paper_id| !folder.paper_ids.iter().any(|id| id == *paper_id))
    {
        return Err("部分论文不在该目录中");
    }
    folder
        .paper_ids
        .retain(|id| !selected.contains(id.as_str()));
    Ok(())
}

fn set_paper_folder_memberships(
    store: &mut FolderStore,
    paper_id: &str,
    folder_ids: &[String],
) -> Result<(), &'static str> {
    let selected: HashSet<&str> = folder_ids.iter().map(String::as_str).collect();
    if selected.len() != folder_ids.len()
        || selected
            .iter()
            .any(|folder_id| !store.folders.iter().any(|folder| folder.id == *folder_id))
    {
        return Err("目录选择无效");
    }
    for folder in &mut store.folders {
        let contains_paper = folder.paper_ids.iter().any(|id| id == paper_id);
        let should_contain = selected.contains(folder.id.as_str());
        match (contains_paper, should_contain) {
            (false, true) => folder.paper_ids.push(paper_id.to_string()),
            (true, false) => folder.paper_ids.retain(|id| id != paper_id),
            _ => {}
        }
    }
    Ok(())
}

fn add_paper_reference(
    app: &tauri::AppHandle,
    folder_id: &str,
    paper_id: &str,
) -> Result<(), String> {
    let _guard = FOLDER_STORE_LOCK
        .lock()
        .map_err(|_| "目录数据锁已损坏".to_string())?;
    let path = folders_path(app)?;
    let mut store = read_folder_store(&path)?;
    let Some(folder) = store
        .folders
        .iter_mut()
        .find(|folder| folder.id == folder_id)
    else {
        // The folder can be deleted while a PDF is being parsed. The paper is
        // still a valid main-library import; only its optional reference is gone.
        return Ok(());
    };
    if !folder.paper_ids.iter().any(|id| id == paper_id) {
        folder.paper_ids.push(paper_id.to_string());
        write_folder_store(&path, &store)?;
    }
    Ok(())
}

fn ensure_folder_exists(app: &tauri::AppHandle, folder_id: &str) -> Result<(), String> {
    let _guard = FOLDER_STORE_LOCK
        .lock()
        .map_err(|_| "目录数据锁已损坏".to_string())?;
    let store = read_folder_store(&folders_path(app)?)?;
    if store.folders.iter().any(|folder| folder.id == folder_id) {
        Ok(())
    } else {
        Err("目标目录不存在".to_string())
    }
}

fn imported_title(file_name: &str, parsed_title: Option<&str>) -> String {
    let fallback = Path::new(file_name)
        .file_stem()
        .and_then(|name| name.to_str())
        .filter(|name| !name.trim().is_empty())
        .unwrap_or(file_name)
        .trim()
        .to_string();
    parsed_title
        .map(str::trim)
        .filter(|title| {
            let normalized = title.to_ascii_lowercase();
            !normalized.is_empty()
                && !matches!(
                    normalized.as_str(),
                    "paper" | "untitled" | "document" | "microsoft word"
                )
                && !normalized.ends_with(".pdf")
        })
        .unwrap_or(fallback.as_str())
        .to_string()
}

#[cfg(test)]
mod title_tests {
    use super::{
        imported_title, remove_folder, remove_paper_reference, remove_paper_references,
        remove_paper_references_from_folder, set_paper_folder_memberships, valid_folder_name,
        FolderStore, PaperFolder,
    };

    #[test]
    fn ignores_placeholder_parser_title_and_uses_filename() {
        assert_eq!(imported_title("my-paper.pdf", Some("paper")), "my-paper");
    }

    #[test]
    fn keeps_meaningful_parser_title() {
        assert_eq!(
            imported_title("my-paper.pdf", Some("A Real Title")),
            "A Real Title"
        );
    }

    #[test]
    fn strips_pdf_extension_when_using_filename_fallback() {
        assert_eq!(imported_title("my-paper.PDF", Some("untitled")), "my-paper");
    }

    #[test]
    fn permanent_delete_removes_every_folder_reference() {
        let mut store = FolderStore {
            folders: vec![
                PaperFolder {
                    id: "folder-a".to_string(),
                    name: "A".to_string(),
                    paper_ids: vec!["paper-1".to_string(), "paper-2".to_string()],
                },
                PaperFolder {
                    id: "folder-b".to_string(),
                    name: "B".to_string(),
                    paper_ids: vec!["paper-1".to_string()],
                },
            ],
        };

        remove_paper_references(&mut store, "paper-1");

        assert_eq!(store.folders[0].paper_ids, vec!["paper-2"]);
        assert!(store.folders[1].paper_ids.is_empty());
    }

    #[test]
    fn custom_folder_removal_only_drops_the_selected_reference() {
        let mut store = FolderStore {
            folders: vec![
                PaperFolder {
                    id: "folder-a".to_string(),
                    name: "A".to_string(),
                    paper_ids: vec!["paper-1".to_string()],
                },
                PaperFolder {
                    id: "folder-b".to_string(),
                    name: "B".to_string(),
                    paper_ids: vec!["paper-1".to_string()],
                },
            ],
        };

        remove_paper_reference(&mut store, "folder-a", "paper-1").unwrap();

        assert!(store.folders[0].paper_ids.is_empty());
        assert_eq!(store.folders[1].paper_ids, vec!["paper-1"]);
    }

    #[test]
    fn batch_folder_removal_only_drops_target_folder_references() {
        let mut store = FolderStore {
            folders: vec![
                PaperFolder {
                    id: "folder-a".to_string(),
                    name: "A".to_string(),
                    paper_ids: vec![
                        "paper-1".to_string(),
                        "paper-2".to_string(),
                        "paper-3".to_string(),
                    ],
                },
                PaperFolder {
                    id: "folder-b".to_string(),
                    name: "B".to_string(),
                    paper_ids: vec!["paper-1".to_string(), "paper-2".to_string()],
                },
            ],
        };

        remove_paper_references_from_folder(
            &mut store,
            "folder-a",
            &["paper-1".to_string(), "paper-2".to_string()],
        )
        .unwrap();

        assert_eq!(store.folders[0].paper_ids, vec!["paper-3"]);
        assert_eq!(store.folders[1].paper_ids, vec!["paper-1", "paper-2"]);
    }

    #[test]
    fn invalid_batch_folder_removal_does_not_mutate_store() {
        let mut store = FolderStore {
            folders: vec![PaperFolder {
                id: "folder-a".to_string(),
                name: "A".to_string(),
                paper_ids: vec!["paper-1".to_string()],
            }],
        };
        let original = store.clone();

        let result = remove_paper_references_from_folder(
            &mut store,
            "folder-a",
            &["paper-1".to_string(), "missing-paper".to_string()],
        );

        assert!(result.is_err());
        assert_eq!(store.folders[0].paper_ids, original.folders[0].paper_ids);
    }

    #[test]
    fn deleting_a_folder_keeps_other_folder_references() {
        let mut store = FolderStore {
            folders: vec![
                PaperFolder {
                    id: "folder-a".to_string(),
                    name: "A".to_string(),
                    paper_ids: vec!["paper-1".to_string()],
                },
                PaperFolder {
                    id: "folder-b".to_string(),
                    name: "B".to_string(),
                    paper_ids: vec!["paper-1".to_string()],
                },
            ],
        };

        assert!(remove_folder(&mut store, "folder-a"));

        assert_eq!(store.folders.len(), 1);
        assert_eq!(store.folders[0].paper_ids, vec!["paper-1"]);
    }

    #[test]
    fn membership_update_can_add_to_multiple_folders_and_transfer() {
        let mut store = FolderStore {
            folders: vec![
                PaperFolder {
                    id: "folder-a".to_string(),
                    name: "A".to_string(),
                    paper_ids: vec!["paper-1".to_string()],
                },
                PaperFolder {
                    id: "folder-b".to_string(),
                    name: "B".to_string(),
                    paper_ids: Vec::new(),
                },
                PaperFolder {
                    id: "folder-c".to_string(),
                    name: "C".to_string(),
                    paper_ids: Vec::new(),
                },
            ],
        };

        set_paper_folder_memberships(
            &mut store,
            "paper-1",
            &["folder-b".to_string(), "folder-c".to_string()],
        )
        .unwrap();

        assert!(store.folders[0].paper_ids.is_empty());
        assert_eq!(store.folders[1].paper_ids, vec!["paper-1"]);
        assert_eq!(store.folders[2].paper_ids, vec!["paper-1"]);
    }

    #[test]
    fn membership_update_rejects_unknown_folders_without_mutation() {
        let mut store = FolderStore {
            folders: vec![PaperFolder {
                id: "folder-a".to_string(),
                name: "A".to_string(),
                paper_ids: vec!["paper-1".to_string()],
            }],
        };
        let original = store.clone();

        let result =
            set_paper_folder_memberships(&mut store, "paper-1", &["missing-folder".to_string()]);

        assert!(result.is_err());
        assert_eq!(store.folders[0].paper_ids, original.folders[0].paper_ids);
    }

    #[test]
    fn folder_names_reject_empty_control_or_overlong_values() {
        assert!(valid_folder_name("机器学习"));
        assert!(!valid_folder_name(""));
        assert!(!valid_folder_name("bad\nname"));
        assert!(!valid_folder_name(&"x".repeat(81)));
    }
}

fn stored_paper_paths(
    app: &tauri::AppHandle,
    paper_path: String,
    parse_path: String,
) -> Result<(PathBuf, PathBuf), String> {
    let root = papers_root(app)?;
    let canonical_root = root
        .canonicalize()
        .map_err(|error| format!("无法定位论文库：{error}"))?;
    let pdf = PathBuf::from(paper_path)
        .canonicalize()
        .map_err(|error| format!("论文文件不存在：{error}"))?;
    let parse = PathBuf::from(parse_path)
        .canonicalize()
        .map_err(|error| format!("解析结果不存在：{error}"))?;
    let same_paper_directory = pdf.parent() == parse.parent();
    let valid_names = pdf.file_name().and_then(|name| name.to_str()) == Some("paper.pdf")
        && parse.file_name().and_then(|name| name.to_str()) == Some("parse.json");
    if !pdf.starts_with(&canonical_root)
        || !parse.starts_with(&canonical_root)
        || !same_paper_directory
        || !valid_names
    {
        return Err("只能读取论文库中的已导入论文".to_string());
    }
    Ok((pdf, parse))
}

fn safe_paper_id(paper_id: &str) -> bool {
    !paper_id.is_empty()
        && paper_id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '-')
}

fn visual_review_queue(parsed: &Value) -> Vec<Value> {
    parsed
        .get("pages")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .flat_map(|page| {
            page.get("visual_review_queue")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .cloned()
        })
        .collect()
}

fn parsed_page_dimensions(parsed: &Value, page: u32) -> Option<(f64, f64)> {
    parsed
        .get("pages")
        .and_then(Value::as_array)
        .and_then(|pages| {
            pages
                .iter()
                .find(|item| item.get("page").and_then(Value::as_u64) == Some(page as u64))
        })
        .and_then(|item| Some((item.get("width")?.as_f64()?, item.get("height")?.as_f64()?)))
}

fn parse_response_from_value(
    paper_id: String,
    pdf_path: &Path,
    parse_path: &Path,
    parsed: &Value,
) -> ParseResponse {
    ParseResponse {
        paper_id,
        paper_path: pdf_path.to_string_lossy().into_owned(),
        parse_path: parse_path.to_string_lossy().into_owned(),
        metadata: parsed.get("metadata").cloned().unwrap_or(Value::Null),
        symbols: parsed
            .get("symbols")
            .cloned()
            .unwrap_or_else(|| Value::Array(vec![])),
        totals: parsed.get("totals").cloned().unwrap_or(Value::Null),
        llm_used: parsed
            .get("llm_used")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        visual_review_queue: visual_review_queue(parsed),
    }
}

fn run_parser(tool: &Path, bundled: bool, pdf: &Path, output: &Path) -> Result<(), String> {
    let mut command = if bundled {
        Command::new(tool)
    } else {
        let mut command = Command::new("python");
        command.arg(tool);
        command
    };
    hide_child_window(&mut command);
    let result = command
        .arg(pdf)
        .arg("--out")
        .arg(output)
        .output()
        .map_err(|error| format!("无法启动本地 Python 解析器：{error}"))?;
    if result.status.success() {
        return Ok(());
    }
    let stdout = String::from_utf8_lossy(&result.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&result.stderr).trim().to_string();
    let detail = if !stdout.is_empty() { stdout } else { stderr };
    Err(format!("PDF 解析失败：{detail}"))
}

fn run_renderer(
    tool: &Path,
    bundled: bool,
    pdf: &Path,
    page: u32,
    output: &Path,
) -> Result<(f64, f64), String> {
    let mut command = if bundled {
        Command::new(tool)
    } else {
        let mut command = Command::new("python");
        command.arg(tool);
        command
    };
    hide_child_window(&mut command);
    let result = command
        .arg(pdf)
        .arg(page.to_string())
        .arg("--out")
        .arg(output)
        .output()
        .map_err(|error| format!("无法启动页面渲染器：{error}"))?;
    if !result.status.success() {
        let detail = String::from_utf8_lossy(&result.stderr).trim().to_string();
        return Err(format!("PDF 页面渲染失败：{detail}"));
    }
    let stdout = String::from_utf8_lossy(&result.stdout);
    let info: Value = serde_json::from_str(stdout.trim())
        .map_err(|error| format!("页面渲染器返回了无效 JSON：{error}"))?;
    let width = info
        .get("width")
        .and_then(Value::as_f64)
        .ok_or("页面宽度缺失")?;
    let height = info
        .get("height")
        .and_then(Value::as_f64)
        .ok_or("页面高度缺失")?;
    Ok((width, height))
}

#[tauri::command]
async fn import_and_parse_pdf(
    app: tauri::AppHandle,
    file_name: String,
    bytes: Vec<u8>,
    folder_id: Option<String>,
) -> Result<ParseResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if bytes.len() < 5 || &bytes[..5] != b"%PDF-" {
            return Err("选择的文件不是有效 PDF（文件头不是 %PDF-）".to_string());
        }
        if let Some(folder_id) = folder_id.as_deref() {
            ensure_folder_exists(&app, folder_id)?;
        }
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_millis();
        let paper_id = format!("local-{timestamp}");
        let root = papers_root(&app)?.join(&paper_id);
        let result = (|| {
            fs::create_dir_all(&root).map_err(|error| format!("无法创建论文目录：{error}"))?;
            let pdf_path = root.join("paper.pdf");
            let parse_path = root.join("parse.json");
            fs::write(&pdf_path, &bytes).map_err(|error| format!("无法保存论文：{error}"))?;
            fs::write(root.join("name.txt"), file_name.as_bytes())
                .map_err(|error| format!("无法保存论文名称：{error}"))?;
            fs::write(
                root.join("title.txt"),
                file_name.trim_end_matches(".pdf").as_bytes(),
            )
            .map_err(|error| format!("无法保存论文标题：{error}"))?;

            let (tool, bundled) = parser_tool(&app)?;
            run_parser(&tool, bundled, &pdf_path, &parse_path)?;
            let parsed_text = fs::read_to_string(&parse_path)
                .map_err(|error| format!("无法读取解析结果：{error}"))?;
            let mut parsed: Value = serde_json::from_str(&parsed_text)
                .map_err(|error| format!("解析器返回了无效 JSON：{error}"))?;
            let parsed_title = parsed
                .get("metadata")
                .and_then(|metadata| metadata.get("title"))
                .and_then(Value::as_str)
                .map(str::trim);
            let title = imported_title(&file_name, parsed_title);
            fs::write(root.join("title.txt"), title.as_bytes())
                .map_err(|error| format!("无法保存解析后的论文标题：{error}"))?;
            if let Some(metadata) = parsed.get_mut("metadata").and_then(Value::as_object_mut) {
                metadata.insert("title".to_string(), Value::String(title));
            }
            if let Some(folder_id) = folder_id.as_deref() {
                add_paper_reference(&app, folder_id, &paper_id)?;
            }
            Ok(parse_response_from_value(
                paper_id,
                &pdf_path,
                &parse_path,
                &parsed,
            ))
        })();
        if result.is_err() {
            // Import is transactional: any failure after the directory is
            // created must not leave a half-imported paper behind.
            let _ = fs::remove_dir_all(&root);
        }
        result
    })
    .await
    .map_err(|error| format!("解析任务异常终止：{error}"))?
}

#[tauri::command]
async fn list_folders(app: tauri::AppHandle) -> Result<Vec<PaperFolder>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = FOLDER_STORE_LOCK
            .lock()
            .map_err(|_| "目录数据锁已损坏".to_string())?;
        Ok(read_folder_store(&folders_path(&app)?)?.folders)
    })
    .await
    .map_err(|error| format!("读取目录任务异常终止：{error}"))?
}

#[tauri::command]
async fn create_folder(app: tauri::AppHandle, name: String) -> Result<PaperFolder, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let name = name.trim();
        if !valid_folder_name(name) {
            return Err("目录名称不能为空、不能包含控制字符，且最多 80 个字符".to_string());
        }
        let _guard = FOLDER_STORE_LOCK
            .lock()
            .map_err(|_| "目录数据锁已损坏".to_string())?;
        let path = folders_path(&app)?;
        let mut store = read_folder_store(&path)?;
        let normalized_name = name.to_lowercase();
        if store
            .folders
            .iter()
            .any(|folder| folder.name.to_lowercase() == normalized_name)
        {
            return Err("已存在同名目录".to_string());
        }
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_millis();
        let mut suffix = 0_u32;
        let id = loop {
            let candidate = if suffix == 0 {
                format!("folder-{timestamp}")
            } else {
                format!("folder-{timestamp}-{suffix}")
            };
            if !store.folders.iter().any(|folder| folder.id == candidate) {
                break candidate;
            }
            suffix += 1;
        };
        let folder = PaperFolder {
            id,
            name: name.to_string(),
            paper_ids: Vec::new(),
        };
        store.folders.push(folder.clone());
        write_folder_store(&path, &store)?;
        Ok(folder)
    })
    .await
    .map_err(|error| format!("创建目录任务异常终止：{error}"))?
}

#[tauri::command]
async fn delete_folder(app: tauri::AppHandle, folder_id: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = FOLDER_STORE_LOCK
            .lock()
            .map_err(|_| "目录数据锁已损坏".to_string())?;
        let path = folders_path(&app)?;
        let mut store = read_folder_store(&path)?;
        if !remove_folder(&mut store, &folder_id) {
            return Err("目录不存在".to_string());
        }
        write_folder_store(&path, &store)
    })
    .await
    .map_err(|error| format!("删除目录任务异常终止：{error}"))?
}

#[tauri::command]
async fn remove_paper_from_folder(
    app: tauri::AppHandle,
    folder_id: String,
    paper_id: String,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        if !safe_paper_id(&paper_id) {
            return Err("论文 ID 无效".to_string());
        }
        let _guard = FOLDER_STORE_LOCK
            .lock()
            .map_err(|_| "目录数据锁已损坏".to_string())?;
        let path = folders_path(&app)?;
        let mut store = read_folder_store(&path)?;
        remove_paper_reference(&mut store, &folder_id, &paper_id).map_err(str::to_string)?;
        write_folder_store(&path, &store)
    })
    .await
    .map_err(|error| format!("移除目录引用任务异常终止：{error}"))?
}

#[tauri::command]
async fn remove_papers_from_folder(
    app: tauri::AppHandle,
    folder_id: String,
    paper_ids: Vec<String>,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        if paper_ids.len() > 512 {
            return Err("一次最多移除 512 篇论文".to_string());
        }
        if paper_ids.iter().any(|paper_id| !safe_paper_id(paper_id)) {
            return Err("论文 ID 无效".to_string());
        }
        let _guard = FOLDER_STORE_LOCK
            .lock()
            .map_err(|_| "目录数据锁已损坏".to_string())?;
        let path = folders_path(&app)?;
        let mut store = read_folder_store(&path)?;
        remove_paper_references_from_folder(&mut store, &folder_id, &paper_ids)
            .map_err(str::to_string)?;
        write_folder_store(&path, &store)
    })
    .await
    .map_err(|error| format!("批量移除目录引用任务异常终止：{error}"))?
}

#[tauri::command]
async fn set_paper_folders(
    app: tauri::AppHandle,
    paper_id: String,
    folder_ids: Vec<String>,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        if !safe_paper_id(&paper_id) {
            return Err("论文 ID 无效".to_string());
        }
        let paper_path = papers_root(&app)?.join(&paper_id);
        if !paper_path.is_dir() {
            return Err("论文不存在或仍在解析中".to_string());
        }
        let _guard = FOLDER_STORE_LOCK
            .lock()
            .map_err(|_| "目录数据锁已损坏".to_string())?;
        let path = folders_path(&app)?;
        let mut store = read_folder_store(&path)?;
        set_paper_folder_memberships(&mut store, &paper_id, &folder_ids).map_err(str::to_string)?;
        write_folder_store(&path, &store)
    })
    .await
    .map_err(|error| format!("更新论文目录任务异常终止：{error}"))?
}

#[tauri::command]
async fn list_papers(app: tauri::AppHandle) -> Result<Vec<StoredPaper>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = papers_root(&app)?;
        if !root.exists() {
            return Ok(Vec::new());
        }
        let mut papers = Vec::new();
        let entries = fs::read_dir(&root).map_err(|error| format!("无法读取论文库：{error}"))?;
        for entry in entries {
            let entry = entry.map_err(|error| format!("无法读取论文目录项：{error}"))?;
            let directory = entry.path();
            if !directory.is_dir() {
                continue;
            }
            let Some(paper_id) = directory.file_name().and_then(|name| name.to_str()) else {
                continue;
            };
            if !safe_paper_id(paper_id) {
                continue;
            }
            let pdf_path = directory.join("paper.pdf");
            let parse_path = directory.join("parse.json");
            if !pdf_path.is_file() || !parse_path.is_file() {
                continue;
            }
            let parsed_text = match fs::read_to_string(&parse_path) {
                Ok(text) => text,
                Err(_) => continue,
            };
            let parsed: Value = match serde_json::from_str(&parsed_text) {
                Ok(value) => value,
                Err(_) => continue,
            };
            let stored_title = fs::read_to_string(directory.join("title.txt"))
                .or_else(|_| fs::read_to_string(directory.join("name.txt")))
                .unwrap_or_else(|_| paper_id.to_string());
            let title = imported_title(
                &fs::read_to_string(directory.join("name.txt"))
                    .unwrap_or_else(|_| paper_id.to_string()),
                Some(&stored_title),
            );
            papers.push(StoredPaper {
                paper_id: paper_id.to_string(),
                paper_path: pdf_path.to_string_lossy().into_owned(),
                parse_path: parse_path.to_string_lossy().into_owned(),
                title: title.trim_end_matches(".pdf").to_string(),
                metadata: parsed.get("metadata").cloned().unwrap_or(Value::Null),
                symbols: parsed
                    .get("symbols")
                    .cloned()
                    .unwrap_or_else(|| Value::Array(Vec::new())),
                totals: parsed.get("totals").cloned().unwrap_or(Value::Null),
                visual_review_queue: visual_review_queue(&parsed),
            });
        }
        // Imported IDs contain a millisecond timestamp, so descending order
        // keeps the library's "最近添加" section truly recent after restart.
        papers.sort_by(|left, right| right.paper_id.cmp(&left.paper_id));
        Ok(papers)
    })
    .await
    .map_err(|error| format!("读取论文库任务异常终止：{error}"))?
}

#[tauri::command]
async fn rename_paper(
    app: tauri::AppHandle,
    paper_id: String,
    title: String,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let title = title.trim();
        if !safe_paper_id(&paper_id) || title.is_empty() || title.len() > 240 {
            return Err("论文 ID 或标题无效".to_string());
        }
        let root = papers_root(&app)?.join(&paper_id);
        if !root.is_dir() {
            return Err("论文不存在".to_string());
        }
        fs::write(root.join("title.txt"), title.as_bytes())
            .map_err(|error| format!("无法保存论文标题：{error}"))
    })
    .await
    .map_err(|error| format!("重命名任务异常终止：{error}"))?
}

#[tauri::command]
async fn update_symbol_meanings(
    app: tauri::AppHandle,
    paper_path: String,
    parse_path: String,
    updates: Vec<SymbolMeaningUpdate>,
    source: Option<String>,
) -> Result<ParseResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let (pdf, parse) = stored_paper_paths(&app, paper_path, parse_path)?;
        if updates.len() > 512 {
            return Err("一次更新的符号数量过多".to_string());
        }
        let parsed_text =
            fs::read_to_string(&parse).map_err(|error| format!("无法读取解析结果：{error}"))?;
        let mut parsed: Value =
            serde_json::from_str(&parsed_text).map_err(|error| format!("解析结果无效：{error}"))?;
        let symbols = parsed
            .get_mut("symbols")
            .and_then(Value::as_array_mut)
            .ok_or("解析结果缺少最终符号池")?;
        let meaning_source = match source.as_deref() {
            Some("qwen") => "qwen",
            Some("deepseek") => "deepseek",
            _ => "remote",
        };
        for update in updates {
            let meaning = update.meaning.trim();
            if update.id.is_empty()
                || update.id.len() > 240
                || meaning.is_empty()
                || meaning.chars().count() > 240
                || meaning.chars().any(char::is_control)
            {
                return Err("符号摘要内容无效".to_string());
            }
            if let Some(symbol) = symbols
                .iter_mut()
                .find(|symbol| symbol.get("id").and_then(Value::as_str) == Some(update.id.as_str()))
            {
                symbol["meaning"] = Value::String(meaning.to_string());
                symbol["meaning_source"] = Value::String(meaning_source.to_string());
            }
        }
        parsed["llm_used"] = Value::Bool(true);
        let serialized = serde_json::to_vec_pretty(&parsed)
            .map_err(|error| format!("无法生成解析结果：{error}"))?;
        fs::write(&parse, serialized).map_err(|error| format!("无法保存符号摘要：{error}"))?;
        let paper_id = pdf
            .parent()
            .and_then(|path| path.file_name())
            .and_then(|name| name.to_str())
            .unwrap_or_default()
            .to_string();
        Ok(parse_response_from_value(paper_id, &pdf, &parse, &parsed))
    })
    .await
    .map_err(|error| format!("保存符号摘要任务异常终止：{error}"))?
}

fn delete_paper_from_library(app: &tauri::AppHandle, paper_id: &str) -> Result<(), String> {
    if !safe_paper_id(paper_id) {
        return Err("论文 ID 无效".to_string());
    }
    let root = papers_root(app)?;
    let target = root.join(paper_id);
    if target.parent() != Some(root.as_path()) || !target.is_dir() {
        return Err("论文不存在".to_string());
    }
    let _guard = FOLDER_STORE_LOCK
        .lock()
        .map_err(|_| "目录数据锁已损坏".to_string())?;
    let path = folders_path(app)?;
    let original_store = read_folder_store(&path)?;
    let mut next_store = original_store.clone();
    remove_paper_references(&mut next_store, paper_id);
    write_folder_store(&path, &next_store)?;
    if let Err(error) = fs::remove_dir_all(target) {
        let _ = write_folder_store(&path, &original_store);
        return Err(format!("无法删除论文：{error}"));
    }
    Ok(())
}

#[tauri::command]
async fn delete_paper(app: tauri::AppHandle, paper_id: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || delete_paper_from_library(&app, &paper_id))
        .await
        .map_err(|error| format!("删除任务异常终止：{error}"))?
}

#[tauri::command]
async fn delete_papers(
    app: tauri::AppHandle,
    paper_ids: Vec<String>,
) -> Result<BatchDeleteResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if paper_ids.len() > 512 {
            return Err("一次最多删除 512 篇论文".to_string());
        }
        let mut seen = HashSet::new();
        let mut result = BatchDeleteResult {
            deleted_ids: Vec::new(),
            failures: Vec::new(),
        };
        for paper_id in paper_ids {
            if !seen.insert(paper_id.clone()) {
                continue;
            }
            match delete_paper_from_library(&app, &paper_id) {
                Ok(()) => result.deleted_ids.push(paper_id),
                Err(error) => result.failures.push(BatchDeleteFailure { paper_id, error }),
            }
        }
        Ok(result)
    })
    .await
    .map_err(|error| format!("批量删除任务异常终止：{error}"))?
}

fn apply_symbol_review(parsed: &mut Value, review_id: &str, surface: &str) -> Result<(), String> {
    let surface = surface.trim();
    if surface.is_empty()
        || surface.chars().count() > 32
        || surface
            .chars()
            .any(|character| character.is_control() || character.is_whitespace())
    {
        return Err("符号不能为空，且不能包含空白或控制字符（最多 32 个字符）".to_string());
    }
    {
        let pages = parsed
            .get_mut("pages")
            .and_then(Value::as_array_mut)
            .ok_or("解析结果缺少页面数据")?;
        let mut resolved: Option<(u64, String, Vec<Value>, String, String, u8, String)> = None;

        for page in pages.iter_mut() {
            let queue = page
                .get_mut("visual_review_queue")
                .and_then(Value::as_array_mut);
            let Some(queue) = queue else { continue };
            let Some(index) = queue
                .iter()
                .position(|item| item.get("review_id").and_then(Value::as_str) == Some(review_id))
            else {
                continue;
            };
            let item = queue.remove(index);
            if item.get("kind").and_then(Value::as_str) != Some("unresolved_glyph_identity") {
                return Err("只有未解析字形可以使用本地符号确认".to_string());
            }
            let page_number = item
                .get("page")
                .and_then(Value::as_u64)
                .or_else(|| page.get("page").and_then(Value::as_u64))
                .ok_or("视觉复核项缺少页码")?;
            let line_id = item
                .get("line_id")
                .and_then(Value::as_str)
                .ok_or("视觉复核项缺少行定位")?
                .to_string();
            let item_bbox = item
                .get("bbox")
                .and_then(Value::as_array)
                .ok_or("视觉复核项缺少位置")?
                .clone();
            let (line_is_definition, line_text, line_bbox) = page
                .get("lines")
                .and_then(Value::as_array)
                .and_then(|lines| {
                    lines.iter().find(|line| {
                        line.get("line_id").and_then(Value::as_str) == Some(line_id.as_str())
                    })
                })
                .map(|line| {
                    (
                        line.get("definition_like")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                        line.get("text")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        line.get("bbox")
                            .cloned()
                            .unwrap_or_else(|| Value::Array(Vec::new())),
                    )
                })
                .unwrap_or((false, String::new(), Value::Array(Vec::new())));
            let evidence_level = if line_is_definition { 3 } else { 2 };
            let evidence_type = if evidence_level >= 3 {
                "direct_definition"
            } else {
                "formula_context"
            }
            .to_string();
            let review_token_id = review_id
                .strip_suffix(":identity")
                .unwrap_or(review_id)
                .to_string();
            let events = page
                .get_mut("definition_events")
                .and_then(Value::as_array_mut)
                .ok_or("解析结果缺少定义事件")?;
            let event_index = events.iter().position(|event| {
                event
                    .get("line_ids")
                    .and_then(Value::as_array)
                    .map(|ids| ids.iter().any(|id| id.as_str() == Some(line_id.as_str())))
                    .unwrap_or(false)
            });
            if event_index.is_none() {
                let visual_event_id = format!("p{page_number}:visual-definition:{review_token_id}");
                events.push(serde_json::json!({
                    "definition_id": visual_event_id,
                    "page": page_number,
                    "line_ids": [line_id],
                    "text": line_text,
                    "bbox": line_bbox,
                    "symbols": [],
                    "trigger": "visual_confirmation",
                }));
            }
            let selected_event_index = event_index.unwrap_or(events.len() - 1);
            let event = events
                .get_mut(selected_event_index)
                .ok_or("无法创建视觉确认定义事件")?;
            let definition_id = event
                .get("definition_id")
                .and_then(Value::as_str)
                .ok_or("定义事件缺少 ID")?
                .to_string();
            let definition_text = event
                .get("text")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let event_symbols = event
                .get_mut("symbols")
                .and_then(Value::as_array_mut)
                .ok_or("定义事件缺少符号列表")?;
            if !event_symbols
                .iter()
                .any(|symbol| symbol.get("surface").and_then(Value::as_str) == Some(surface))
            {
                event_symbols.push(serde_json::json!({
                    "surface": surface,
                    "token_ids": [review_token_id],
                    "identity_status": "visual_confirmed",
                    "evidence_level": evidence_level,
                    "evidence_types": [evidence_type.clone()],
                }));
            }
            resolved = Some((
                page_number,
                line_id,
                item_bbox,
                definition_id,
                definition_text,
                evidence_level,
                evidence_type,
            ));
            break;
        }

        let Some((
            page_number,
            line_id,
            item_bbox,
            definition_id,
            definition_text,
            evidence_level,
            evidence_type,
        )) = resolved
        else {
            return Err("找不到这条视觉复核项，可能已被其他操作处理".to_string());
        };
        let occurrence = serde_json::json!({
            "token_id": format!("visual:{review_id}"),
            "page": page_number,
            "line_id": line_id,
            "bbox": item_bbox,
        });
        let symbols = parsed
            .get_mut("symbols")
            .and_then(Value::as_array_mut)
            .ok_or("解析结果缺少最终符号池")?;
        if let Some(symbol) = symbols
            .iter_mut()
            .find(|symbol| symbol.get("surface").and_then(Value::as_str) == Some(surface))
        {
            let previous_status = symbol
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("review")
                .to_string();
            symbol["identity_status"] = Value::String("visual_confirmed".to_string());
            let previous_evidence_level = symbol
                .get("evidence_level")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            symbol["evidence_level"] =
                serde_json::json!(previous_evidence_level.max(evidence_level as u64));
            symbol["status"] = Value::String(
                if evidence_level >= 3 || previous_status == "confirmed" {
                    "confirmed"
                } else {
                    "review"
                }
                .to_string(),
            );
            let locations = symbol
                .get_mut("occurrence_locations")
                .and_then(Value::as_array_mut)
                .ok_or("符号缺少出现位置")?;
            locations.push(occurrence.clone());
            let count = locations.len() as u64;
            symbol["occurrences"] = serde_json::json!(count);
            if let Some(events) = symbol
                .get_mut("definition_events")
                .and_then(Value::as_array_mut)
            {
                if !events
                    .iter()
                    .any(|event| event.as_str() == Some(definition_id.as_str()))
                {
                    events.push(Value::String(definition_id.clone()));
                }
            }
            if let Some(types) = symbol
                .get_mut("evidence_types")
                .and_then(Value::as_array_mut)
            {
                if !types
                    .iter()
                    .any(|item| item.as_str() == Some(evidence_type.as_str()))
                {
                    types.push(Value::String(evidence_type.clone()));
                }
            }
        } else {
            let symbol_id = format!("symbol:{surface}:{}", symbols.len());
            symbols.push(serde_json::json!({
                "id": symbol_id,
                "surface": surface,
                "status": if evidence_level >= 3 { "confirmed" } else { "review" },
                "evidence_level": evidence_level,
                "evidence_types": [evidence_type],
                "definition_events": [definition_id],
                "occurrences": 1,
                "occurrence_locations": [occurrence],
                "definition": definition_text,
                "location": format!("第 {page_number} 页"),
                "meaning": "作者定义的数学符号",
                "meaning_source": "local",
            }));
        }
        if let Some(resolutions) = parsed
            .get_mut("review_resolutions")
            .and_then(Value::as_array_mut)
        {
            resolutions.push(serde_json::json!({ "review_id": review_id, "surface": surface }));
        } else {
            parsed["review_resolutions"] =
                serde_json::json!([{ "review_id": review_id, "surface": surface }]);
        }
        let pending = visual_review_queue(&parsed).len();
        let review_symbols = parsed
            .get("symbols")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter(|item| item.get("status").and_then(Value::as_str) == Some("review"))
                    .count()
            })
            .unwrap_or(0);
        if let Some(totals) = parsed.get_mut("totals") {
            totals["visual_review_items"] = serde_json::json!(pending);
            totals["review_symbols"] = serde_json::json!(review_symbols);
        }
    }
    Ok(())
}

fn remove_visual_review_item(parsed: &mut Value, review_id: &str) -> Result<(), String> {
    let pages = parsed
        .get_mut("pages")
        .and_then(Value::as_array_mut)
        .ok_or("解析结果缺少页面数据")?;
    let mut dismissed = false;
    for page in pages.iter_mut() {
        let Some(queue) = page
            .get_mut("visual_review_queue")
            .and_then(Value::as_array_mut)
        else {
            continue;
        };
        let Some(index) = queue
            .iter()
            .position(|item| item.get("review_id").and_then(Value::as_str) == Some(review_id))
        else {
            continue;
        };
        if queue[index].get("kind").and_then(Value::as_str) != Some("unresolved_glyph_identity") {
            return Err("只有未解析字形可以被忽略".to_string());
        }
        queue.remove(index);
        dismissed = true;
        break;
    }
    if !dismissed {
        return Err("找不到这条视觉复核项，可能已被其他操作处理".to_string());
    }
    if let Some(resolutions) = parsed
        .get_mut("review_resolutions")
        .and_then(Value::as_array_mut)
    {
        resolutions.push(serde_json::json!({ "review_id": review_id, "ignored": true }));
    } else {
        parsed["review_resolutions"] = serde_json::json!([{
            "review_id": review_id,
            "ignored": true
        }]);
    }
    let pending = visual_review_queue(parsed).len();
    if let Some(totals) = parsed.get_mut("totals") {
        totals["visual_review_items"] = serde_json::json!(pending);
    }
    Ok(())
}

#[tauri::command]
async fn confirm_symbol_review(
    app: tauri::AppHandle,
    paper_path: String,
    parse_path: String,
    review_id: String,
    surface: String,
) -> Result<ParseResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let (pdf, parse) = stored_paper_paths(&app, paper_path, parse_path)?;
        let parsed_text =
            fs::read_to_string(&parse).map_err(|error| format!("无法读取解析结果：{error}"))?;
        let mut parsed: Value =
            serde_json::from_str(&parsed_text).map_err(|error| format!("解析结果无效：{error}"))?;
        apply_symbol_review(&mut parsed, &review_id, &surface)?;
        let serialized = serde_json::to_vec_pretty(&parsed)
            .map_err(|error| format!("无法生成解析结果：{error}"))?;
        fs::write(&parse, serialized).map_err(|error| format!("无法保存符号确认结果：{error}"))?;
        let paper_id = pdf
            .parent()
            .and_then(|path| path.file_name())
            .and_then(|name| name.to_str())
            .unwrap_or_default()
            .to_string();
        Ok(parse_response_from_value(paper_id, &pdf, &parse, &parsed))
    })
    .await
    .map_err(|error| format!("符号确认任务异常终止：{error}"))?
}

#[tauri::command]
async fn dismiss_symbol_review(
    app: tauri::AppHandle,
    paper_path: String,
    parse_path: String,
    review_id: String,
) -> Result<ParseResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let (pdf, parse) = stored_paper_paths(&app, paper_path, parse_path)?;
        let parsed_text =
            fs::read_to_string(&parse).map_err(|error| format!("无法读取解析结果：{error}"))?;
        let mut parsed: Value =
            serde_json::from_str(&parsed_text).map_err(|error| format!("解析结果无效：{error}"))?;
        remove_visual_review_item(&mut parsed, &review_id)?;
        let serialized = serde_json::to_vec_pretty(&parsed)
            .map_err(|error| format!("无法生成解析结果：{error}"))?;
        fs::write(&parse, serialized).map_err(|error| format!("无法保存视觉复核结果：{error}"))?;
        let paper_id = pdf
            .parent()
            .and_then(|path| path.file_name())
            .and_then(|name| name.to_str())
            .unwrap_or_default()
            .to_string();
        Ok(parse_response_from_value(paper_id, &pdf, &parse, &parsed))
    })
    .await
    .map_err(|error| format!("忽略视觉复核任务异常终止：{error}"))?
}

#[tauri::command]
async fn render_pdf_page(
    app: tauri::AppHandle,
    paper_path: String,
    parse_path: String,
    page: u32,
) -> Result<RenderResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let (pdf, parse) = stored_paper_paths(&app, paper_path, parse_path)?;
        let parsed_text =
            fs::read_to_string(&parse).map_err(|error| format!("无法读取解析结果：{error}"))?;
        let parsed: Value =
            serde_json::from_str(&parsed_text).map_err(|error| format!("解析结果无效：{error}"))?;
        let output_dir = pdf.parent().ok_or("论文路径没有父目录")?.join("rendered");
        fs::create_dir_all(&output_dir)
            .map_err(|error| format!("无法创建页面缓存目录：{error}"))?;
        // The cache suffix invalidates pages rendered with the previous
        // low-resolution scale without deleting user data.
        let output = output_dir.join(format!("page-{page}-hd.png"));
        let (page_width, page_height, image) = match fs::read(&output) {
            Ok(image) => {
                let (width, height) = parsed_page_dimensions(&parsed, page)
                    .ok_or_else(|| format!("页面 {page} 不存在或缺少尺寸信息"))?;
                (width, height, image)
            }
            Err(_) => {
                // Multiple thumbnails and the main page can request the same
                // page concurrently. Each renderer gets a private file, then
                // publishes it with rename so readers never observe a
                // partially written PNG.
                let nonce = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_err(|error| error.to_string())?
                    .as_nanos();
                let temporary = output_dir.join(format!(
                    ".page-{page}-{nonce}-{}.tmp.png",
                    std::process::id()
                ));
                let (tool, bundled) = render_tool(&app)?;
                let rendered = run_renderer(&tool, bundled, &pdf, page, &temporary);
                let dimensions = match rendered {
                    Ok(dimensions) => dimensions,
                    Err(error) => {
                        let _ = fs::remove_file(&temporary);
                        return Err(error);
                    }
                };
                let image = match fs::read(&temporary) {
                    Ok(image) => image,
                    Err(error) => {
                        let _ = fs::remove_file(&temporary);
                        return Err(format!("无法读取渲染页面：{error}"));
                    }
                };
                if fs::rename(&temporary, &output).is_err() {
                    // Another request may have published the same cache file
                    // first. The private image is still complete and safe to
                    // return for this request.
                    let _ = fs::remove_file(&temporary);
                }
                (dimensions.0, dimensions.1, image)
            }
        };
        let encoded = encode_base64(&image);
        let occurrences = parsed
            .get("symbols")
            .and_then(Value::as_array)
            .map(|symbols| {
                symbols
                    .iter()
                    .flat_map(|symbol| {
                        let surface = symbol.get("surface").and_then(Value::as_str).unwrap_or("");
                        symbol
                            .get("occurrence_locations")
                            .and_then(Value::as_array)
                            .into_iter()
                            .flatten()
                            .filter_map(move |location| {
                                if location.get("page").and_then(Value::as_u64) != Some(page as u64)
                                {
                                    return None;
                                }
                                let bbox = location.get("bbox")?.clone();
                                let identity_key = symbol
                                    .get("identity_key")
                                    .and_then(Value::as_str)
                                    .unwrap_or(surface);
                                let style =
                                    symbol.get("style").and_then(Value::as_str).unwrap_or("");
                                Some(serde_json::json!({
                                    "surface": surface,
                                    "identityKey": identity_key,
                                    "style": style,
                                    "bbox": bbox
                                }))
                            })
                    })
                    .collect()
            })
            .unwrap_or_default();
        let _ = app;
        Ok(RenderResponse {
            image_data: format!("data:image/png;base64,{encoded}"),
            page_width,
            page_height,
            occurrences,
        })
    })
    .await
    .map_err(|error| format!("页面渲染任务异常终止：{error}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_key_provider_names_are_restricted() {
        assert!(valid_api_key_provider("qwen"));
        assert!(valid_api_key_provider("deepseek"));
        assert!(valid_api_key_provider("custom"));
        assert!(!valid_api_key_provider("../other"));
    }

    #[test]
    fn official_model_endpoints_cannot_be_overridden() {
        assert_eq!(
            model_endpoint("qwen", Some("https://attacker.invalid"))
                .unwrap()
                .as_str(),
            QWEN_CHAT_ENDPOINT
        );
        assert_eq!(
            model_endpoint("deepseek", Some("https://attacker.invalid"))
                .unwrap()
                .as_str(),
            DEEPSEEK_CHAT_ENDPOINT
        );
    }

    #[test]
    fn custom_model_endpoint_requires_https_except_on_loopback() {
        assert!(model_endpoint("custom", Some("https://models.example/v1/chat")).is_ok());
        assert!(model_endpoint("custom", Some("http://localhost:11434/v1/chat")).is_ok());
        assert!(model_endpoint("custom", Some("http://127.0.0.1:11434/v1/chat")).is_ok());
        assert!(model_endpoint("custom", Some("http://models.example/v1/chat")).is_err());
        assert!(
            model_endpoint("custom", Some("https://user:secret@models.example/v1/chat")).is_err()
        );
    }

    fn review_fixture(direct_definition: bool) -> Value {
        serde_json::json!({
            "pages": [{
                "page": 1,
                "lines": [{
                    "line_id": "p1:line2",
                    "definition_like": direct_definition,
                    "formula_like": true
                }],
                "definition_events": [{
                    "definition_id": "p1:definition2",
                    "line_ids": ["p1:line2"],
                    "text": "where α is the regularization weight",
                    "symbols": []
                }],
                "visual_review_queue": [{
                    "review_id": "p1:char7:identity",
                    "kind": "unresolved_glyph_identity",
                    "page": 1,
                    "line_id": "p1:line2",
                    "bbox": [10.0, 20.0, 15.0, 30.0],
                    "surface": "(cid:42)",
                    "reason": "unresolved"
                }]
            }],
            "symbols": [],
            "totals": {
                "visual_review_items": 1,
                "review_symbols": 0
            }
        })
    }

    #[test]
    fn direct_visual_confirmation_enters_confirmed_pool() {
        let mut parsed = review_fixture(true);
        apply_symbol_review(&mut parsed, "p1:char7:identity", "α").unwrap();

        let symbol = &parsed["symbols"][0];
        assert_eq!(symbol["surface"], "α");
        assert_eq!(symbol["status"], "confirmed");
        assert_eq!(symbol["evidence_level"], 3);
        assert_eq!(symbol["occurrences"], 1);
        assert_eq!(
            parsed["pages"][0]["visual_review_queue"]
                .as_array()
                .unwrap()
                .len(),
            0
        );
        assert_eq!(parsed["totals"]["visual_review_items"], 0);
    }

    #[test]
    fn formula_visual_confirmation_remains_reviewable() {
        let mut parsed = review_fixture(false);
        apply_symbol_review(&mut parsed, "p1:char7:identity", "α").unwrap();

        let symbol = &parsed["symbols"][0];
        assert_eq!(symbol["status"], "review");
        assert_eq!(symbol["evidence_level"], 2);
        assert_eq!(parsed["totals"]["review_symbols"], 1);
    }

    #[test]
    fn visual_confirmation_upgrades_an_existing_unresolved_symbol() {
        let mut parsed = review_fixture(true);
        parsed["symbols"] = serde_json::json!([{
            "id": "symbol:alpha:0",
            "surface": "α",
            "status": "unresolved",
            "evidence_level": 1,
            "evidence_types": ["event_context"],
            "definition_events": [],
            "occurrences": 0,
            "occurrence_locations": []
        }]);

        apply_symbol_review(&mut parsed, "p1:char7:identity", "α").unwrap();

        let symbol = &parsed["symbols"][0];
        assert_eq!(symbol["status"], "confirmed");
        assert_eq!(symbol["identity_status"], "visual_confirmed");
        assert_eq!(symbol["evidence_level"], 3);
        assert_eq!(symbol["occurrences"], 1);
    }

    #[test]
    fn dismissing_visual_review_removes_only_the_queue_item() {
        let mut parsed = review_fixture(true);
        remove_visual_review_item(&mut parsed, "p1:char7:identity").unwrap();

        assert!(parsed["symbols"].as_array().unwrap().is_empty());
        assert!(parsed["pages"][0]["visual_review_queue"]
            .as_array()
            .unwrap()
            .is_empty());
        assert_eq!(parsed["totals"]["visual_review_items"], 0);
        assert_eq!(parsed["review_resolutions"][0]["ignored"], true);
    }

    #[test]
    fn visual_confirmation_rejects_unknown_review_id_without_mutating_symbols() {
        let mut parsed = review_fixture(true);
        let error = apply_symbol_review(&mut parsed, "missing:identity", "α").unwrap_err();

        assert!(error.contains("找不到"));
        assert!(parsed["symbols"].as_array().unwrap().is_empty());
    }

    #[test]
    fn orphan_visual_confirmation_creates_a_traceable_definition_event() {
        let mut parsed = review_fixture(true);
        parsed["pages"][0]["definition_events"] = serde_json::json!([]);

        apply_symbol_review(&mut parsed, "p1:char7:identity", "α").unwrap();

        let event = &parsed["pages"][0]["definition_events"][0];
        assert_eq!(event["trigger"], "visual_confirmation");
        assert_eq!(event["line_ids"][0], "p1:line2");
        assert_eq!(
            parsed["symbols"][0]["definition_events"][0],
            event["definition_id"]
        );
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            get_api_key_status,
            set_api_key,
            request_model_completion,
            import_and_parse_pdf,
            list_papers,
            list_folders,
            create_folder,
            delete_folder,
            remove_paper_from_folder,
            remove_papers_from_folder,
            set_paper_folders,
            rename_paper,
            update_symbol_meanings,
            delete_paper,
            delete_papers,
            confirm_symbol_review,
            dismiss_symbol_review,
            render_pdf_page
        ])
        .run(tauri::generate_context!())
        .expect("error while running PhiReader");
}
