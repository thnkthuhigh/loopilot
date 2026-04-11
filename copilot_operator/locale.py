"""Locale — i18n strings for operator CLI display."""

from __future__ import annotations

__all__ = ['get_locale', 'set_locale', 'T']

_CURRENT_LOCALE = 'en'

_STRINGS: dict[str, dict[str, str]] = {
    # --- format_operator_status (watch/status) ---
    'status_header': {
        'en': '=== Copilot Operator Status ===',
        'vi': '=== Trạng Thái Copilot Operator ===',
    },
    'status': {
        'en': 'Status',
        'vi': 'Trạng thái',
    },
    'goal': {
        'en': 'Goal',
        'vi': 'Mục tiêu',
    },
    'goal_profile': {
        'en': 'Goal profile',
        'vi': 'Hồ sơ mục tiêu',
    },
    'run_id': {
        'en': 'Run ID',
        'vi': 'Mã chạy',
    },
    'iterations_completed': {
        'en': 'Iterations completed',
        'vi': 'Số vòng lặp hoàn thành',
    },
    'workspace_ecosystem': {
        'en': 'Workspace ecosystem',
        'vi': 'Hệ sinh thái dự án',
    },
    'package_manager': {
        'en': 'Package manager',
        'vi': 'Quản lý gói',
    },
    'plan_summary': {
        'en': 'Plan summary',
        'vi': 'Tóm tắt kế hoạch',
    },
    'current_milestone': {
        'en': 'Current milestone',
        'vi': 'Milestone đang làm',
    },
    'next_milestone': {
        'en': 'Next milestone',
        'vi': 'Milestone tiếp theo',
    },
    'current_task': {
        'en': 'Current task',
        'vi': 'Tác vụ đang làm',
    },
    'next_task': {
        'en': 'Next task',
        'vi': 'Tác vụ tiếp theo',
    },
    'milestone_counts': {
        'en': 'Milestone counts',
        'vi': 'Số milestone',
    },
    'task_counts': {
        'en': 'Task counts',
        'vi': 'Số tác vụ',
    },
    'last_iteration': {
        'en': 'Last iteration',
        'vi': 'Vòng lặp gần nhất',
    },
    'last_score': {
        'en': 'Last score',
        'vi': 'Điểm gần nhất',
    },
    'last_session_id': {
        'en': 'Last session ID',
        'vi': 'Mã phiên gần nhất',
    },
    'run_log_dir': {
        'en': 'Run log directory',
        'vi': 'Thư mục log',
    },
    'updated_at': {
        'en': 'Updated at',
        'vi': 'Cập nhật lúc',
    },
    'pending_action': {
        'en': 'Pending action',
        'vi': 'Hành động chờ',
    },
    'pending_code': {
        'en': 'Pending code',
        'vi': 'Mã trạng thái chờ',
    },
    'pending_reason': {
        'en': 'Pending reason',
        'vi': 'Lý do chờ',
    },
    'next_prompt': {
        'en': 'Next prompt',
        'vi': 'Prompt tiếp theo',
    },
    'final_code': {
        'en': 'Final code',
        'vi': 'Mã kết thúc',
    },
    'final_reason': {
        'en': 'Final reason',
        'vi': 'Lý do kết thúc',
    },
    'last_decision_code': {
        'en': 'Last decision code',
        'vi': 'Mã quyết định gần nhất',
    },
    'last_summary': {
        'en': 'Last summary',
        'vi': 'Tóm tắt gần nhất',
    },
    'milestones': {
        'en': 'Milestones',
        'vi': 'Các milestone',
    },
    'artifacts': {
        'en': 'Artifacts',
        'vi': 'Kết quả lưu trữ',
    },
    'none_yet': {
        'en': '(none yet)',
        'vi': '(chưa có)',
    },
    'not_started': {
        'en': '(not started)',
        'vi': '(chưa bắt đầu)',
    },

    # --- focus ---
    'focus_header': {
        'en': '=== Copilot Operator Focus ===',
        'vi': '=== Đang Tập Trung ===',
    },
    'next_baton': {
        'en': 'Next baton',
        'vi': 'Bước tiếp theo',
    },

    # --- live output ---
    'starting_iteration': {
        'en': 'Starting iteration',
        'vi': 'Bắt đầu vòng lặp',
    },
    'running_validations': {
        'en': 'Running validations...',
        'vi': 'Đang kiểm tra...',
    },
    'file_changed': {
        'en': 'file changed',
        'vi': 'file thay đổi',
    },
    'files_changed': {
        'en': 'files changed',
        'vi': 'file thay đổi',
    },
    'validations': {
        'en': 'validations',
        'vi': 'kiểm tra',
    },

    # --- status values ---
    'status_running': {
        'en': 'running',
        'vi': 'đang chạy',
    },
    'status_done': {
        'en': 'done',
        'vi': 'hoàn thành',
    },
    'status_blocked': {
        'en': 'blocked',
        'vi': 'bị chặn',
    },
    'status_error': {
        'en': 'error',
        'vi': 'lỗi',
    },
    'status_not_started': {
        'en': 'not_started',
        'vi': 'chưa bắt đầu',
    },

    # --- explain ---
    'run_summary': {
        'en': 'RUN SUMMARY',
        'vi': 'TÓM TẮT KẾT QUẢ',
    },
    'result': {
        'en': 'Result',
        'vi': 'Kết quả',
    },
    'success': {
        'en': 'SUCCESS',
        'vi': 'THÀNH CÔNG',
    },
}


def get_locale() -> str:
    """Return the current locale code."""
    return _CURRENT_LOCALE


def set_locale(lang: str) -> None:
    """Set the current locale. Supported: 'en', 'vi'."""
    global _CURRENT_LOCALE
    _CURRENT_LOCALE = lang.lower().strip()


def T(key: str, **kwargs: str) -> str:
    """Translate a key using the current locale. Falls back to English."""
    strings = _STRINGS.get(key)
    if not strings:
        return key
    text = strings.get(_CURRENT_LOCALE, strings.get('en', key))
    if kwargs:
        text = text.format(**kwargs)
    return text
