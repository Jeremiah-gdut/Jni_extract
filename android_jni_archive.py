from __future__ import annotations

from pathlib import Path

from binaryninja import (
    BinaryView,
    BinaryViewType,
    PluginCommand,
    Settings,
    TypeArchive,
    log_error,
    log_info,
    log_warn,
)


SETTINGS_GROUP = "androidJniArchive"
AUTO_KEY = f"{SETTINGS_GROUP}.autoApply"
FORCE_KEY = f"{SETTINGS_GROUP}.forceThisView"
ARCHIVE_KEY = f"{SETTINGS_GROUP}.archivePath"

settings = Settings()


def _plugin_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_archive_path() -> str:
    return str(_plugin_dir() / "jni.bnta")


def _register_settings() -> None:
    settings.register_group(SETTINGS_GROUP, "Android JNI Archive")

    if not settings.contains(AUTO_KEY):
        settings.register_setting(
            AUTO_KEY,
            r'''
            {
                "title" : "Auto apply JNI archive",
                "type" : "boolean",
                "default" : true,
                "description" : "Automatically attach jni.bnta and pull all types for likely Android JNI shared libraries."
            }
            '''
        )

    if not settings.contains(FORCE_KEY):
        settings.register_setting(
            FORCE_KEY,
            r'''
            {
                "title" : "Force current view as Android JNI library",
                "type" : "boolean",
                "default" : false,
                "description" : "Force auto-loading of jni.bnta for this BinaryView."
            }
            '''
        )

    if not settings.contains(ARCHIVE_KEY):
        settings.register_setting(
            ARCHIVE_KEY,
            rf'''
            {{
                "title" : "JNI archive path",
                "type" : "string",
                "default" : "{_default_archive_path().replace("\\", "\\\\")}",
                "description" : "Path to jni.bnta"
            }}
            '''
        )


def _get_archive_path(bv: BinaryView) -> Path:
    path = settings.get_string(ARCHIVE_KEY, bv)
    if not path:
        path = _default_archive_path()
    return Path(path)


def _is_likely_android_jni_so(bv: BinaryView) -> bool:
    if bv.view_type != "ELF":
        return False

    filename = ""
    if getattr(bv.file, "original_filename", None):
        filename = bv.file.original_filename
    elif getattr(bv.file, "filename", None):
        filename = bv.file.filename

    lower_name = filename.lower()

    # 手工启发式：
    # 1. ELF + .so
    # 2. 导出了 JNI_OnLoad
    # 3. 存在 Java_/JNI_ 风格符号
    if lower_name.endswith(".so") or ".so." in lower_name:
        return True

    if bv.get_symbol_by_raw_name("JNI_OnLoad") is not None:
        return True

    for sym in bv.get_symbols():
        raw = getattr(sym, "raw_name", None) or getattr(sym, "name", "")
        if raw.startswith("Java_") or raw.startswith("JNI_"):
            return True

    return False


def _should_apply_automatically(bv: BinaryView) -> bool:
    if settings.get_bool(FORCE_KEY, bv):
        return True

    if not settings.get_bool(AUTO_KEY, bv):
        return False

    return _is_likely_android_jni_so(bv)


def _already_attached_same_archive(bv: BinaryView, archive: TypeArchive) -> bool:
    attached = bv.attached_type_archives
    if not attached:
        return False

    archive_id = archive.id
    archive_path = str(Path(archive.path).resolve()) if archive.path else None

    if archive_id and archive_id in attached:
        return True

    if archive_path:
        for _, path in attached.items():
            try:
                if Path(path).resolve() == Path(archive_path):
                    return True
            except Exception:
                pass

    return False


def _attach_and_pull_all_types(bv: BinaryView) -> bool:
    archive_path = _get_archive_path(bv)
    if not archive_path.exists():
        log_error(f"[android-jni-archive] archive not found: {archive_path}")
        return False

    archive = TypeArchive.open(str(archive_path))
    if archive is None:
        log_error(f"[android-jni-archive] failed to open archive: {archive_path}")
        return False

    if not _already_attached_same_archive(bv, archive):
        bv.attach_type_archive(archive)

    names = list(archive.type_names)
    if not names:
        log_warn(f"[android-jni-archive] archive contains no types: {archive_path}")
        return False

    result = bv.pull_types_from_archive(archive, names)
    if result is None:
        log_error("[android-jni-archive] pull_types_from_archive failed")
        return False

    log_info(
        f"[android-jni-archive] attached archive and pulled {len(names)} types from {archive_path.name}"
    )
    return True


def _on_binaryview_finalized(bv: BinaryView) -> None:
    try:
        if not _should_apply_automatically(bv):
            return
        _attach_and_pull_all_types(bv)
    except Exception as e:
        log_error(f"[android-jni-archive] finalized hook failed: {e}")


def _manual_apply(bv: BinaryView) -> None:
    try:
        _attach_and_pull_all_types(bv)
    except Exception as e:
        log_error(f"[android-jni-archive] manual apply failed: {e}")


_register_settings()

# 必须保持模块级引用，不能让回调对象被垃圾回收
BinaryViewType.add_binaryview_finalized_event(_on_binaryview_finalized)

PluginCommand.register(
    "Android JNI\\Attach jni.bnta and Pull All Types",
    "Attach jni.bnta to the current BinaryView and pull all JNI types from the archive",
    _manual_apply,
)