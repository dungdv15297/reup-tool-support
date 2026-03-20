import asyncio
import os
import sys
import threading
from datetime import datetime

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:  # pragma: no cover
    QAudioOutput = None
    QMediaPlayer = None

from app_state import load_state, save_state
from srt_utils import parse_srt, serialize_srt
from translator_service import (
    LANGUAGE_OPTIONS,
    apply_translations_to_subs,
    build_job_hash,
    build_resume_payload,
    build_srt_items,
    estimate_job_tokens,
    list_google_models,
    parse_resume_payload,
    render_plain_text,
    translate_items,
    translate_plain_text_to_items,
)
from tts_service import (
    DEFAULT_PREVIEW_TEXT,
    apply_runtime_settings,
    clean_subtitle_text,
    estimate_speech_duration_seconds,
    format_duration_estimate,
    list_tts_capabilities,
    preview_voice,
    process_srt_logic,
    process_text_only,
)


def build_timestamp_name(prefix: str, extension: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%d%m%y_%H%M%S')}.{extension}"


class App(QMainWindow):
    tts_progress_signal = pyqtSignal(int, str)
    tts_finished_signal = pyqtSignal(str, bool)
    translator_progress_signal = pyqtSignal(int, str)
    translator_finished_signal = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reup Tool Support")
        self.setMinimumSize(1280, 900)

        self.state = load_state()
        self.capability_map = {}
        self.tts_selected_srt_path = ""
        self.tts_srt_content = None
        self.translator_selected_srt_path = ""
        self.translator_srt_content = None
        self._restoring_state = False
        self.tts_cancel_requested = False
        self.translator_cancel_requested = False
        self.tts_loop = None
        self.tts_task = None
        self.translator_loop = None
        self.translator_task = None
        self.current_preview_path = None

        self.player = None
        self.audio_output = None
        if QMediaPlayer and QAudioOutput:
            self.player = QMediaPlayer()
            self.audio_output = QAudioOutput()
            self.player.setAudioOutput(self.audio_output)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(12)

        self.build_left_menu(root_layout)
        self.build_main_panel(root_layout)

        self.tts_progress_signal.connect(self._update_tts_progress_ui)
        self.tts_finished_signal.connect(self._tts_finished_ui)
        self.translator_progress_signal.connect(self._update_translator_progress_ui)
        self.translator_finished_signal.connect(self._translator_finished_ui)

        self.restore_saved_state()
        self.load_capabilities()

    def build_left_menu(self, root_layout):
        menu_container = QWidget()
        menu_container.setFixedWidth(220)
        menu_layout = QVBoxLayout(menu_container)
        menu_layout.setContentsMargins(12, 12, 12, 12)
        menu_layout.setSpacing(10)

        title = QLabel("Reup Tool")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        menu_layout.addWidget(title)

        subtitle = QLabel("Menu chức năng")
        subtitle.setStyleSheet("color: #64748b;")
        menu_layout.addWidget(subtitle)

        self.btn_open_settings = QPushButton("⚙ Settings")
        self.btn_open_settings.clicked.connect(self.open_settings_dialog)
        menu_layout.addWidget(self.btn_open_settings)

        self.btn_menu_translator = QPushButton("Phiên dịch viên")
        self.btn_menu_translator.setCheckable(True)
        self.btn_menu_translator.setChecked(True)
        self.btn_menu_translator.clicked.connect(lambda: self.switch_page(0))
        menu_layout.addWidget(self.btn_menu_translator)

        self.btn_menu_tts = QPushButton("Thuyết minh viên")
        self.btn_menu_tts.setCheckable(True)
        self.btn_menu_tts.clicked.connect(lambda: self.switch_page(1))
        menu_layout.addWidget(self.btn_menu_tts)

        menu_layout.addStretch(1)
        root_layout.addWidget(menu_container)

    def build_main_panel(self, root_layout):
        self.page_stack = QStackedWidget()
        root_layout.addWidget(self.page_stack, 1)
        self.page_stack.addWidget(self.build_translator_page())
        self.page_stack.addWidget(self.build_narrator_page())
        self.settings_dialog = QDialog(self)
        self.settings_dialog.setWindowTitle("Settings")
        self.settings_dialog.setModal(True)
        self.settings_dialog.resize(820, 760)
        settings_layout = QVBoxLayout(self.settings_dialog)
        settings_layout.setContentsMargins(12, 12, 12, 12)
        settings_layout.addWidget(self.build_config_page())

    def switch_page(self, index: int):
        self.page_stack.setCurrentIndex(index)
        self.btn_menu_translator.setChecked(index == 0)
        self.btn_menu_tts.setChecked(index == 1)

    def build_config_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        header = QLabel("Cấu hình")
        header.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(header)

        description = QLabel(
            "Thiết lập dùng chung cho toàn bộ tool: nơi lưu file output, API key cho LLM và nền tảng TTS."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #475569;")
        layout.addWidget(description)

        output_group = QGroupBox("Nơi lưu trữ file output")
        output_form = QFormLayout(output_group)
        self.tts_output_dir_label = QLabel("chưa chọn.")
        self.tts_output_dir_label.setWordWrap(True)
        self.btn_select_tts_output_dir = QPushButton("Chọn folder")
        self.btn_select_tts_output_dir.clicked.connect(self.select_tts_output_dir)
        tts_output_row = QHBoxLayout()
        tts_output_row.addWidget(self.tts_output_dir_label)
        tts_output_row.addWidget(self.btn_select_tts_output_dir)
        output_form.addRow("Thuyết minh viên:", tts_output_row)

        self.translator_output_dir_label = QLabel("chưa chọn.")
        self.translator_output_dir_label.setWordWrap(True)
        self.btn_select_translator_output_dir = QPushButton("Chọn folder")
        self.btn_select_translator_output_dir.clicked.connect(self.select_translator_output_dir)
        translator_output_row = QHBoxLayout()
        translator_output_row.addWidget(self.translator_output_dir_label)
        translator_output_row.addWidget(self.btn_select_translator_output_dir)
        output_form.addRow("Phiên dịch viên:", translator_output_row)
        layout.addWidget(output_group)

        tts_group = QGroupBox("API / Credentials cho nền tảng TTS")
        tts_form = QFormLayout(tts_group)
        self.google_api_key = QLineEdit()
        self.google_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.google_api_key.editingFinished.connect(self.persist_state)
        tts_form.addRow("Google Cloud TTS API key:", self.google_api_key)
        self.aws_access_key = QLineEdit()
        self.aws_access_key.editingFinished.connect(self.persist_state)
        tts_form.addRow("AWS access key:", self.aws_access_key)
        self.aws_secret_key = QLineEdit()
        self.aws_secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.aws_secret_key.editingFinished.connect(self.persist_state)
        tts_form.addRow("AWS secret key:", self.aws_secret_key)
        self.aws_region = QLineEdit()
        self.aws_region.editingFinished.connect(self.persist_state)
        tts_form.addRow("AWS region:", self.aws_region)
        self.vbee_api_token = QLineEdit()
        self.vbee_api_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.vbee_api_token.editingFinished.connect(self.persist_state)
        tts_form.addRow("Vbee token:", self.vbee_api_token)
        self.vbee_tts_url = QLineEdit()
        self.vbee_tts_url.editingFinished.connect(self.persist_state)
        tts_form.addRow("Vbee URL:", self.vbee_tts_url)
        self.vbee_app_id = QLineEdit()
        self.vbee_app_id.editingFinished.connect(self.persist_state)
        tts_form.addRow("Vbee app id:", self.vbee_app_id)
        self.vbee_response_mode = QComboBox()
        self.vbee_response_mode.addItems(["auto", "binary"])
        self.vbee_response_mode.currentIndexChanged.connect(self.persist_state)
        tts_form.addRow("Vbee response mode:", self.vbee_response_mode)
        self.vbee_voices_json = QTextEdit()
        self.vbee_voices_json.setMaximumHeight(90)
        self.vbee_voices_json.textChanged.connect(self.persist_state)
        tts_form.addRow("Vbee voices json:", self.vbee_voices_json)
        layout.addWidget(tts_group)

        llm_group = QGroupBox("API Key cho LLM")
        llm_form = QFormLayout(llm_group)
        self.translator_deepseek_api_key = QLineEdit()
        self.translator_deepseek_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.translator_deepseek_api_key.editingFinished.connect(self.persist_state)
        llm_form.addRow("DeepSeek API key:", self.translator_deepseek_api_key)
        self.translator_google_api_key = QLineEdit()
        self.translator_google_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.translator_google_api_key.editingFinished.connect(self.persist_state)
        llm_form.addRow("Google Gemini API key:", self.translator_google_api_key)
        layout.addWidget(llm_group)

        action_row = QHBoxLayout()
        self.btn_save_global_config = QPushButton("Lưu cấu hình dùng chung")
        self.btn_save_global_config.clicked.connect(self.save_global_config)
        action_row.addWidget(self.btn_save_global_config)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        layout.addStretch(1)
        return page

    def open_settings_dialog(self):
        self.settings_dialog.exec()

    def build_narrator_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        header = QLabel("Thuyết minh viên")
        header.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(header)

        description = QLabel(
            "Tạo giọng thuyết minh từ văn bản hoặc SRT, có chọn provider, voice, tốc độ đọc và nghe thử ngay trong local app."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #475569;")
        layout.addWidget(description)

        self.setup_tts_controls(layout)
        self.setup_tts_tabs(layout)
        self.setup_tts_progress(layout)
        self.setup_tts_log(layout)

        layout.setStretchFactor(self.tts_tabs, 3)
        layout.setStretchFactor(self.tts_log_group, 4)
        return page

    def setup_tts_controls(self, parent_layout):
        group = QGroupBox("Thiết lập thuyết minh")
        group.setMaximumHeight(150)
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.currentIndexChanged.connect(self.on_provider_changed)
        row.addWidget(self.provider_combo)

        self.voice_combo = QComboBox()
        self.voice_combo.currentIndexChanged.connect(self.persist_state)
        row.addWidget(self.voice_combo)

        speed_label = QLabel("Tốc độ:")
        row.addWidget(speed_label)
        self.speed_combo = QComboBox()
        for raw_speed in range(9, 21):
            speed = raw_speed / 10
            self.speed_combo.addItem(f"{speed:.1f}x", speed)
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)
        row.addWidget(self.speed_combo)

        self.btn_preview = QPushButton("Nghe thử voice")
        self.btn_preview.clicked.connect(self.handle_preview)
        row.addWidget(self.btn_preview)

        layout.addLayout(row)

        action_row = QHBoxLayout()
        self.btn_refresh = QPushButton("Tải lại voices")
        self.btn_refresh.clicked.connect(self.load_capabilities)
        action_row.addWidget(self.btn_refresh)

        self.btn_open_tts_output_dir = QPushButton("Mở folder output")
        self.btn_open_tts_output_dir.clicked.connect(self.open_tts_output_dir)
        self.btn_open_tts_output_dir.setEnabled(False)
        action_row.addWidget(self.btn_open_tts_output_dir)
        action_row.addStretch(1)

        self.provider_note = QLabel("Đang tải danh sách provider...")
        self.provider_note.setStyleSheet("color: #475569;")
        self.provider_note.setWordWrap(True)

        layout.addLayout(action_row)
        layout.addWidget(self.provider_note)
        parent_layout.addWidget(group)

    def setup_tts_tabs(self, parent_layout):
        self.tts_tabs = QTabWidget()
        parent_layout.addWidget(self.tts_tabs, 3)

        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        text_layout.addWidget(QLabel("Nhập văn bản cần chuyển đổi:"))
        self.textbox = QTextEdit()
        self.textbox.textChanged.connect(self.on_tts_text_changed)
        text_layout.addWidget(self.textbox)
        self.tts_text_estimate_label = QLabel("Estimate thời gian đọc: chưa có dữ liệu.")
        self.tts_text_estimate_label.setWordWrap(True)
        self.tts_text_estimate_label.setStyleSheet("color: #475569;")
        text_layout.addWidget(self.tts_text_estimate_label)
        text_layout.addWidget(QLabel("Nội dung nghe thử:"))
        self.preview_textbox = QTextEdit()
        self.preview_textbox.setMaximumHeight(110)
        self.preview_textbox.textChanged.connect(self.persist_state)
        text_layout.addWidget(self.preview_textbox)
        self.btn_gen_txt = QPushButton("Generate & Save MP3")
        self.btn_gen_txt.clicked.connect(self.handle_text_gen)
        text_layout.addWidget(self.btn_gen_txt)
        self.tts_tabs.addTab(text_tab, "Nhập văn bản")

        srt_tab = QWidget()
        srt_layout = QVBoxLayout(srt_tab)
        srt_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.btn_select_srt = QPushButton("Chọn file .srt")
        self.btn_select_srt.clicked.connect(self.select_tts_srt_file)
        srt_layout.addWidget(self.btn_select_srt)
        self.srt_info_label = QLabel("Chưa chọn file.")
        self.srt_info_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.srt_info_label.setWordWrap(True)
        srt_layout.addWidget(self.srt_info_label)
        self.tts_srt_estimate_label = QLabel("Estimate thời gian đọc file SRT: chưa có dữ liệu.")
        self.tts_srt_estimate_label.setWordWrap(True)
        self.tts_srt_estimate_label.setStyleSheet("color: #475569;")
        srt_layout.addWidget(self.tts_srt_estimate_label)
        self.btn_gen_srt = QPushButton("Generate Synchronized MP3")
        self.btn_gen_srt.clicked.connect(self.handle_srt_gen)
        srt_layout.addWidget(self.btn_gen_srt)
        self.tts_tabs.addTab(srt_tab, "Xử lý SRT")
        self.tts_tabs.setCurrentIndex(1)

    def setup_tts_progress(self, parent_layout):
        group = QGroupBox("Tiến trình")
        layout = QVBoxLayout(group)
        self.tts_progress_label = QLabel("Sẵn sàng...")
        layout.addWidget(self.tts_progress_label)
        row = QHBoxLayout()
        self.tts_progress_bar = QProgressBar()
        self.tts_progress_bar.setRange(0, 100)
        row.addWidget(self.tts_progress_bar, 1)
        self.btn_stop_tts = QPushButton("Dừng")
        self.btn_stop_tts.clicked.connect(self.stop_tts_process)
        row.addWidget(self.btn_stop_tts)
        layout.addLayout(row)
        parent_layout.addWidget(group)

    def setup_tts_log(self, parent_layout):
        self.tts_log_group = QGroupBox("Log phiên")
        layout = QVBoxLayout(self.tts_log_group)
        self.tts_log = QTextEdit()
        self.tts_log.setReadOnly(True)
        layout.addWidget(self.tts_log)
        parent_layout.addWidget(self.tts_log_group, 4)

    def build_translator_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        header = QLabel("Phiên dịch viên")
        header.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(header)

        description = QLabel(
            "Dịch văn bản hoặc phụ đề sang tiếng Việt bằng DeepSeek, có batching theo ngữ cảnh, retry khi rate-limit và hỗ trợ dịch tiếp từ batch dở."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #475569;")
        layout.addWidget(description)

        self.setup_translator_settings(layout)
        self.setup_translator_tabs(layout)
        self.setup_translator_progress(layout)
        self.setup_translator_log(layout)

        layout.setStretchFactor(self.translator_tabs, 3)
        layout.setStretchFactor(self.translator_log_group, 4)
        return page

    def setup_translator_settings(self, parent_layout):
        group = QGroupBox("Thiết lập phiên dịch")
        layout = QVBoxLayout(group)
        layout.setSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        self.translator_provider_combo = QComboBox()
        self.translator_provider_combo.addItem("DeepSeek", "deepseek")
        self.translator_provider_combo.addItem("Google Gemini", "google")
        self.translator_provider_combo.currentIndexChanged.connect(self.on_translator_provider_changed)
        grid.addWidget(QLabel("LLM provider:"), 0, 0)
        grid.addWidget(self.translator_provider_combo, 0, 1)

        self.translator_model_combo = QComboBox()
        self.translator_model_combo.currentIndexChanged.connect(self.persist_state)
        grid.addWidget(QLabel("LLM:"), 0, 2)
        grid.addWidget(self.translator_model_combo, 0, 3)

        self.btn_load_translator_models = QPushButton("Load models")
        self.btn_load_translator_models.clicked.connect(self.handle_load_translator_models)

        self.source_lang_combo = QComboBox()
        for code, label in LANGUAGE_OPTIONS.items():
            self.source_lang_combo.addItem(label, code)
        self.source_lang_combo.currentIndexChanged.connect(self.persist_state)
        grid.addWidget(QLabel("Ngôn ngữ nguồn:"), 1, 0)
        grid.addWidget(self.source_lang_combo, 1, 1)

        self.target_lang_combo = QComboBox()
        self.target_lang_combo.addItem(LANGUAGE_OPTIONS["vi"], "vi")
        self.target_lang_combo.currentIndexChanged.connect(self.persist_state)
        grid.addWidget(QLabel("Ngôn ngữ đích:"), 1, 2)
        grid.addWidget(self.target_lang_combo, 1, 3)

        self.translator_estimate_label = QLabel("Estimate tokens: chưa có dữ liệu.")
        self.translator_estimate_label.setWordWrap(True)
        self.translator_estimate_label.setStyleSheet("color: #475569;")
        grid.addWidget(QLabel("Estimate:"), 2, 0)
        grid.addWidget(self.translator_estimate_label, 2, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        self.btn_load_translator_models.setMinimumWidth(140)
        btn_row.addWidget(self.btn_load_translator_models)
        self.btn_resume_translation = QPushButton("Dịch tiếp bản gần nhất")
        self.btn_resume_translation.clicked.connect(self.handle_resume_translation)
        btn_row.addWidget(self.btn_resume_translation)
        self.btn_open_translator_output_dir = QPushButton("Mở folder output")
        self.btn_open_translator_output_dir.clicked.connect(self.open_translator_output_dir)
        self.btn_open_translator_output_dir.setEnabled(False)
        btn_row.addWidget(self.btn_open_translator_output_dir)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        parent_layout.addWidget(group)

    def setup_translator_tabs(self, parent_layout):
        self.translator_tabs = QTabWidget()
        parent_layout.addWidget(self.translator_tabs, 3)

        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        text_layout.addWidget(QLabel("Nhập văn bản cần dịch:"))
        self.translator_textbox = QTextEdit()
        self.translator_textbox.textChanged.connect(self.on_translator_text_changed)
        text_layout.addWidget(self.translator_textbox)
        self.btn_translate_text = QPushButton("Dịch văn bản")
        self.btn_translate_text.clicked.connect(self.handle_translate_text)
        text_layout.addWidget(self.btn_translate_text)
        self.translator_tabs.addTab(text_tab, "Văn bản")

        srt_tab = QWidget()
        srt_layout = QVBoxLayout(srt_tab)
        srt_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.btn_select_translator_srt = QPushButton("Chọn file .srt")
        self.btn_select_translator_srt.clicked.connect(self.select_translator_srt_file)
        srt_layout.addWidget(self.btn_select_translator_srt)
        self.translator_srt_info_label = QLabel("Chưa chọn file.")
        self.translator_srt_info_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.translator_srt_info_label.setWordWrap(True)
        srt_layout.addWidget(self.translator_srt_info_label)
        self.translator_srt_estimate_label = QLabel("Estimate tokens file SRT: chưa có dữ liệu.")
        self.translator_srt_estimate_label.setWordWrap(True)
        self.translator_srt_estimate_label.setStyleSheet("color: #475569;")
        srt_layout.addWidget(self.translator_srt_estimate_label)
        self.btn_translate_srt = QPushButton("Dịch file SRT")
        self.btn_translate_srt.clicked.connect(self.handle_translate_srt)
        srt_layout.addWidget(self.btn_translate_srt)
        self.translator_tabs.addTab(srt_tab, "File SRT")
        self.translator_tabs.setCurrentIndex(1)

    def setup_translator_progress(self, parent_layout):
        group = QGroupBox("Tiến trình dịch")
        layout = QVBoxLayout(group)
        self.translator_progress_label = QLabel("Sẵn sàng...")
        layout.addWidget(self.translator_progress_label)
        row = QHBoxLayout()
        self.translator_progress_bar = QProgressBar()
        self.translator_progress_bar.setRange(0, 100)
        row.addWidget(self.translator_progress_bar, 1)
        self.btn_stop_translation = QPushButton("Dừng")
        self.btn_stop_translation.clicked.connect(self.stop_translator_process)
        row.addWidget(self.btn_stop_translation)
        layout.addLayout(row)
        parent_layout.addWidget(group)

    def setup_translator_log(self, parent_layout):
        self.translator_log_group = QGroupBox("Log phiên dịch")
        layout = QVBoxLayout(self.translator_log_group)
        self.translator_log = QTextEdit()
        self.translator_log.setReadOnly(True)
        layout.addWidget(self.translator_log)
        parent_layout.addWidget(self.translator_log_group, 4)

    def restore_saved_state(self):
        self._restoring_state = True
        credentials = self.state.get("credentials", {})
        tts_state = self.state.get("tts", {})
        translator_state = self.state.get("translator", {})

        google = credentials.get("google", {})
        polly = credentials.get("amazon_polly", {})
        vbee = credentials.get("vbee", {})
        self.google_api_key.setText(google.get("api_key", ""))
        self.aws_access_key.setText(polly.get("aws_access_key_id", ""))
        self.aws_secret_key.setText(polly.get("aws_secret_access_key", ""))
        self.aws_region.setText(polly.get("aws_region", "us-east-1"))
        self.vbee_api_token.setText(vbee.get("api_token", ""))
        self.vbee_tts_url.setText(vbee.get("tts_url", ""))
        self.vbee_app_id.setText(vbee.get("app_id", ""))
        self.vbee_response_mode.setCurrentText(vbee.get("response_mode", "auto"))
        self.vbee_voices_json.setPlainText(vbee.get("voices_json", "[]"))

        self.textbox.setPlainText(tts_state.get("text_input", ""))
        self.preview_textbox.setPlainText(tts_state.get("preview_text", "") or DEFAULT_PREVIEW_TEXT)
        self.set_tts_output_dir(tts_state.get("output_dir", ""))
        self.tts_selected_srt_path = tts_state.get("selected_srt_path", "")
        if self.tts_selected_srt_path and os.path.exists(self.tts_selected_srt_path):
            self.load_tts_srt_file(self.tts_selected_srt_path)
        else:
            self.srt_info_label.setText("Chưa chọn file.")
        self.tts_log.clear()
        for item in tts_state.get("logs", []):
            self.tts_log.append(item)

        selected_provider = translator_state.get("selected_llm_provider", "deepseek")
        self.translator_provider_combo.setCurrentIndex(max(0, self.translator_provider_combo.findData(selected_provider)))
        self.refresh_translator_provider_ui()
        self.translator_model_combo.setCurrentText(translator_state.get("selected_llm_model", "deepseek-chat"))
        self.translator_deepseek_api_key.setText(translator_state.get("api_keys", {}).get("deepseek", ""))
        self.translator_google_api_key.setText(translator_state.get("api_keys", {}).get("google", ""))
        self.source_lang_combo.setCurrentIndex(max(0, self.source_lang_combo.findData(translator_state.get("preferences", {}).get("source_lang", "auto"))))
        self.target_lang_combo.setCurrentIndex(max(0, self.target_lang_combo.findData(translator_state.get("preferences", {}).get("target_lang", "vi"))))
        self.translator_textbox.setPlainText(translator_state.get("text_input", ""))
        self.set_translator_output_dir(translator_state.get("output_dir", ""))
        self.translator_selected_srt_path = translator_state.get("selected_srt_path", "")
        if self.translator_selected_srt_path and os.path.exists(self.translator_selected_srt_path):
            self.load_translator_srt_file(self.translator_selected_srt_path)
        else:
            self.translator_srt_info_label.setText("Chưa chọn file.")
        self.translator_log.clear()
        for item in translator_state.get("logs", []):
            self.translator_log.append(item)

        apply_runtime_settings(credentials)
        self._restoring_state = False
        self.update_tts_estimate_labels()
        self.update_translator_estimate_labels()

    def build_state_from_ui(self):
        tts_logs = [self.tts_log.document().findBlockByNumber(i).text().strip() for i in range(self.tts_log.document().blockCount())]
        translator_logs = [
            self.translator_log.document().findBlockByNumber(i).text().strip()
            for i in range(self.translator_log.document().blockCount())
        ]
        return {
            "credentials": {
                "google": {"api_key": self.google_api_key.text().strip()},
                "amazon_polly": {
                    "aws_access_key_id": self.aws_access_key.text().strip(),
                    "aws_secret_access_key": self.aws_secret_key.text().strip(),
                    "aws_region": self.aws_region.text().strip() or "us-east-1",
                },
                "vbee": {
                    "api_token": self.vbee_api_token.text().strip(),
                    "tts_url": self.vbee_tts_url.text().strip(),
                    "app_id": self.vbee_app_id.text().strip(),
                    "response_mode": self.vbee_response_mode.currentText(),
                    "voices_json": self.vbee_voices_json.toPlainText().strip() or "[]",
                },
            },
            "tts": {
                "provider_id": self.provider_combo.currentData() or "edge",
                "voice_id": self.voice_combo.currentData() or "",
                "speed": self.speed_combo.currentData() or 1.0,
                "text_input": self.textbox.toPlainText(),
                "preview_text": self.preview_textbox.toPlainText(),
                "selected_srt_path": self.tts_selected_srt_path or "",
                "output_dir": getattr(self, "tts_output_dir", ""),
                "logs": [item for item in tts_logs if item][-80:],
            },
            "translator": {
                "selected_llm_provider": self.translator_provider_combo.currentData() or "deepseek",
                "selected_llm_model": self.translator_model_combo.currentText(),
                "api_keys": {
                    "deepseek": self.translator_deepseek_api_key.text().strip(),
                    "google": self.translator_google_api_key.text().strip(),
                },
                "preferences": {
                    "source_lang": self.source_lang_combo.currentData() or "auto",
                    "target_lang": self.target_lang_combo.currentData() or "vi",
                },
                "text_input": self.translator_textbox.toPlainText(),
                "selected_srt_path": self.translator_selected_srt_path or "",
                "output_dir": getattr(self, "translator_output_dir", ""),
                "logs": [item for item in translator_logs if item][-100:],
                "resume": self.state.get("translator", {}).get("resume", {}),
            },
        }

    def refresh_translator_provider_ui(self):
        provider = self.translator_provider_combo.currentData() or "deepseek"
        saved_translator = self.state.get("translator", {})
        models_map = {
            "deepseek": ["deepseek-chat", "deepseek-reasoner"],
            "google": [],
        }
        saved_keys = self.state.get("translator", {}).get("api_keys", {})
        current_model = self.translator_model_combo.currentText()
        self.translator_model_combo.blockSignals(True)
        self.translator_model_combo.clear()
        self.translator_model_combo.addItems(models_map.get(provider, []))
        if provider == "google" and not models_map.get(provider):
            saved_model = saved_translator.get("selected_llm_model", "")
            if saved_model.startswith("gemini-"):
                self.translator_model_combo.addItem(saved_model)
        model_index = self.translator_model_combo.findText(current_model)
        if model_index >= 0:
            self.translator_model_combo.setCurrentIndex(model_index)
        self.translator_model_combo.blockSignals(False)
        self.btn_load_translator_models.setVisible(provider == "google")
        self.persist_state()

    def on_translator_provider_changed(self):
        self.refresh_translator_provider_ui()

    def handle_load_translator_models(self):
        provider = self.translator_provider_combo.currentData() or "deepseek"
        if provider != "google":
            return
        api_key = self.translator_google_api_key.text().strip()
        try:
            models = list_google_models(api_key)
            if not models:
                raise RuntimeError("Không lấy được model Gemini khả dụng từ API.")
            self.translator_model_combo.clear()
            self.translator_model_combo.addItems(models)
            self.append_translator_log(f"Đã load {len(models)} model Google Gemini từ API.")
            self.persist_state()
        except Exception as exc:
            QMessageBox.critical(self, "Lỗi", str(exc))
            self.append_translator_log(f"Lỗi load model Google: {exc}")

    def update_translator_estimate_labels(self):
        text = self.translator_textbox.toPlainText()
        if text.strip():
            items = translate_plain_text_to_items(text)
            estimate = estimate_job_tokens(items)
            self.translator_estimate_label.setText(
                f"{estimate['items']} dòng, {estimate['batches']} batch, ~{estimate['total_tokens']} tokens "
                f"(input ~{estimate['input_tokens']}, output ~{estimate['output_tokens']})"
            )
        else:
            self.translator_estimate_label.setText("Estimate tokens: chưa có dữ liệu.")

        if self.translator_srt_content:
            try:
                subs = parse_srt(self.translator_srt_content)
                items = build_srt_items(subs)
                estimate = estimate_job_tokens(items)
                self.translator_srt_estimate_label.setText(
                    f"{estimate['items']} dòng, {estimate['batches']} batch, ~{estimate['total_tokens']} tokens "
                    f"(max batch ~{estimate['max_batch_tokens']})"
                )
            except Exception:
                self.translator_srt_estimate_label.setText("Estimate tokens file SRT: không đọc được nội dung.")
        else:
            self.translator_srt_estimate_label.setText("Estimate tokens file SRT: chưa có dữ liệu.")

    def update_tts_estimate_labels(self):
        speed = self.speed_combo.currentData() or 1.0
        text = self.textbox.toPlainText().strip()
        if text:
            seconds = estimate_speech_duration_seconds(text, speed)
            self.tts_text_estimate_label.setText(
                f"Estimate thời gian đọc: ~{format_duration_estimate(seconds)} ở tốc độ {speed:.1f}x."
            )
        else:
            self.tts_text_estimate_label.setText("Estimate thời gian đọc: chưa có dữ liệu.")

        if self.tts_srt_content:
            try:
                subs = parse_srt(self.tts_srt_content)
                spoken_text = "\n".join(
                    clean_subtitle_text(block.text) for block in subs if clean_subtitle_text(block.text)
                ).strip()
                speech_seconds = estimate_speech_duration_seconds(spoken_text, speed)
                timeline_seconds = 0
                if subs:
                    last = subs[-1].end
                    timeline_seconds = (
                        last.hours * 3600 + last.minutes * 60 + last.seconds + last.milliseconds / 1000
                    )
                message = f"Estimate thời gian đọc file SRT: ~{format_duration_estimate(speech_seconds)} ở tốc độ {speed:.1f}x."
                if timeline_seconds > 0:
                    message += f" Timeline gốc ~{format_duration_estimate(timeline_seconds)}."
                self.tts_srt_estimate_label.setText(message)
            except Exception:
                self.tts_srt_estimate_label.setText("Estimate thời gian đọc file SRT: không đọc được nội dung.")
        else:
            self.tts_srt_estimate_label.setText("Estimate thời gian đọc file SRT: chưa có dữ liệu.")

    def on_translator_text_changed(self):
        self.persist_state()
        self.update_translator_estimate_labels()

    def persist_state(self):
        if self._restoring_state:
            return
        self.state = self.build_state_from_ui()
        save_state(self.state)

    def load_capabilities(self):
        apply_runtime_settings(self.state.get("credentials", {}))
        self.capability_map = {item["id"]: item for item in list_tts_capabilities()}
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for provider_id, info in self.capability_map.items():
            self.provider_combo.addItem(info["label"], provider_id)
        self.provider_combo.blockSignals(False)
        self.restore_provider_selection()
        self.refresh_provider_ui()

    def restore_provider_selection(self):
        provider_id = self.state.get("tts", {}).get("provider_id", "edge")
        index = self.provider_combo.findData(provider_id)
        self.provider_combo.setCurrentIndex(max(index, 0))

    def refresh_provider_ui(self):
        provider = self.capability_map.get(self.provider_combo.currentData())
        if not provider:
            return

        saved_tts = self.state.get("tts", {})
        saved_provider = saved_tts.get("provider_id", "edge")
        saved_voice = saved_tts.get("voice_id", "") if saved_provider == provider["id"] else ""
        saved_speed = saved_tts.get("speed", 1.0)

        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for voice in provider["voices"]:
            self.voice_combo.addItem(voice["label"], voice["id"])
        voice_index = self.voice_combo.findData(saved_voice)
        if voice_index >= 0:
            self.voice_combo.setCurrentIndex(voice_index)
        self.voice_combo.blockSignals(False)

        self.speed_combo.blockSignals(True)
        speed_index = self.speed_combo.findData(round(float(saved_speed), 1))
        if speed_index >= 0:
            self.speed_combo.setCurrentIndex(speed_index)
        self.speed_combo.blockSignals(False)

        status = provider["status"]
        note = f'{provider["label"]}: {status["message"] or "Sẵn sàng."} Cau hinh API/key trong popup Settings.'
        if provider["id"] == "amazon_polly":
            note += " Amazon Polly hiện chưa có voice tiếng Việt chính thức."
        self.provider_note.setText(note)

        if not self.preview_textbox.toPlainText().strip():
            self.preview_textbox.blockSignals(True)
            self.preview_textbox.setPlainText(DEFAULT_PREVIEW_TEXT)
            self.preview_textbox.blockSignals(False)

        ready = status["configured"] == "true" and self.voice_combo.count() > 0
        self.btn_preview.setEnabled(ready)
        self.btn_gen_txt.setEnabled(ready)
        self.btn_gen_srt.setEnabled(ready and self.tts_srt_content is not None)
        self.update_tts_estimate_labels()
        self.persist_state()

    def append_tts_log(self, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.tts_log.append(f"[{timestamp}] {message}")
        self.persist_state()

    def append_translator_log(self, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.translator_log.append(f"[{timestamp}] {message}")
        self.persist_state()

    def set_tts_output_dir(self, path: str):
        self.tts_output_dir = path or ""
        label = self.tts_output_dir if self.tts_output_dir else "chưa chọn."
        self.tts_output_dir_label.setText(f"Folder output: {label}")
        if hasattr(self, "btn_open_tts_output_dir"):
            self.btn_open_tts_output_dir.setEnabled(bool(self.tts_output_dir and os.path.isdir(self.tts_output_dir)))

    def set_translator_output_dir(self, path: str):
        self.translator_output_dir = path or ""
        label = self.translator_output_dir if self.translator_output_dir else "chưa chọn."
        self.translator_output_dir_label.setText(f"Folder output: {label}")
        if hasattr(self, "btn_open_translator_output_dir"):
            self.btn_open_translator_output_dir.setEnabled(
                bool(self.translator_output_dir and os.path.isdir(self.translator_output_dir))
            )

    def select_tts_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Chọn folder output cho Thuyết minh viên")
        if path:
            self.set_tts_output_dir(path)
            self.persist_state()

    def select_translator_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Chọn folder output cho Phiên dịch viên")
        if path:
            self.set_translator_output_dir(path)
            self.persist_state()

    def _open_output_dir(self, path: str):
        if not path:
            QMessageBox.warning(self, "Cảnh báo", "Bạn chưa chọn folder output.")
            return
        if not os.path.isdir(path):
            QMessageBox.warning(self, "Cảnh báo", "Folder output hiện không tồn tại.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_tts_output_dir(self):
        self._open_output_dir(getattr(self, "tts_output_dir", ""))

    def open_translator_output_dir(self):
        self._open_output_dir(getattr(self, "translator_output_dir", ""))

    def _remove_temp_file(self, path: str | None):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def load_tts_srt_file(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as handle:
            self.tts_srt_content = handle.read()
        self.tts_selected_srt_path = file_path
        self.srt_info_label.setText(f"Đã chọn: {os.path.basename(file_path)}")
        self.update_tts_estimate_labels()
        self.persist_state()

    def select_tts_srt_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Chọn file SRT", "", "SRT files (*.srt)")
        if file_path:
            self.load_tts_srt_file(file_path)
            self.refresh_provider_ui()

    def load_translator_srt_file(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as handle:
            self.translator_srt_content = handle.read()
        self.translator_selected_srt_path = file_path
        self.translator_srt_info_label.setText(f"Đã chọn: {os.path.basename(file_path)}")
        self.persist_state()
        self.update_translator_estimate_labels()

    def select_translator_srt_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Chọn file SRT", "", "SRT files (*.srt)")
        if file_path:
            self.load_translator_srt_file(file_path)

    def on_provider_changed(self):
        self.persist_state()
        self.refresh_provider_ui()

    def on_speed_changed(self):
        self.update_tts_estimate_labels()
        self.persist_state()

    def on_tts_text_changed(self):
        self.update_tts_estimate_labels()
        self.persist_state()

    def save_global_config(self):
        self.persist_state()
        apply_runtime_settings(self.state.get("credentials", {}))
        self.append_tts_log("Đã lưu cấu hình dùng chung.")
        self.append_translator_log("Đã lưu cấu hình dùng chung.")
        self.load_capabilities()

    def _run_async(self, coro):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _run_tracked_async(self, coro, mode: str):
        loop = asyncio.new_event_loop()
        task = None
        try:
            asyncio.set_event_loop(loop)
            task = loop.create_task(coro)
            if mode == "tts":
                self.tts_loop = loop
                self.tts_task = task
            else:
                self.translator_loop = loop
                self.translator_task = task
            return loop.run_until_complete(task)
        finally:
            if mode == "tts":
                self.tts_task = None
                self.tts_loop = None
            else:
                self.translator_task = None
                self.translator_loop = None
            loop.close()

    def stop_tts_process(self):
        self.tts_cancel_requested = True
        self.append_tts_log("Đã gửi yêu cầu dừng xử lý TTS.")
        if self.tts_loop and self.tts_task:
            try:
                self.tts_loop.call_soon_threadsafe(self.tts_task.cancel)
            except RuntimeError:
                pass

    def stop_translator_process(self):
        self.translator_cancel_requested = True
        self.append_translator_log("Đã gửi yêu cầu dừng xử lý dịch.")
        if self.translator_loop and self.translator_task:
            try:
                self.translator_loop.call_soon_threadsafe(self.translator_task.cancel)
            except RuntimeError:
                pass

    def _resolve_output_path(self, folder: str, filename: str) -> str:
        if not folder:
            raise RuntimeError("Bạn chưa chọn folder output.")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, filename)

    def handle_preview(self):
        provider_id = self.provider_combo.currentData()
        voice_id = self.voice_combo.currentData()
        speed = self.speed_combo.currentData() or 1.0
        text = self.preview_textbox.toPlainText().strip() or DEFAULT_PREVIEW_TEXT
        self.start_background(lambda: self.run_tts_preview(provider_id, voice_id, speed, text), "tts")

    def handle_text_gen(self):
        text = self.textbox.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập văn bản!")
            return
        try:
            save_path = self._resolve_output_path(getattr(self, "tts_output_dir", ""), build_timestamp_name("tts_output", "mp3"))
        except Exception as exc:
            QMessageBox.warning(self, "Cảnh báo", str(exc))
            return
        self.start_background(
            lambda: self.run_tts_text(
                text,
                self.provider_combo.currentData(),
                self.voice_combo.currentData(),
                self.speed_combo.currentData() or 1.0,
                save_path,
            ),
            "tts",
        )

    def handle_srt_gen(self):
        if not self.tts_srt_content:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn file SRT!")
            return
        try:
            save_path = self._resolve_output_path(getattr(self, "tts_output_dir", ""), build_timestamp_name("tts_output", "mp3"))
        except Exception as exc:
            QMessageBox.warning(self, "Cảnh báo", str(exc))
            return
        self.start_background(
            lambda: self.run_tts_srt(
                self.tts_srt_content,
                self.provider_combo.currentData(),
                self.voice_combo.currentData(),
                self.speed_combo.currentData() or 1.0,
                save_path,
            ),
            "tts",
        )

    def run_tts_preview(self, provider_id, voice_id, speed, text):
        try:
            preview_path = self._run_tracked_async(
                preview_voice(
                    provider_id=provider_id,
                    voice_id=voice_id,
                    speed=speed,
                    sample_text=text,
                    progress_callback=lambda p, t: self.tts_progress_signal.emit(int(p * 100), t),
                    cancel_callback=lambda: self.tts_cancel_requested,
                ),
                "tts",
            )
            if self.player and self.audio_output:
                old_preview = self.current_preview_path
                self.current_preview_path = preview_path
                self.player.setSource(QUrl.fromLocalFile(preview_path))
                self.player.play()
                if old_preview and old_preview != preview_path:
                    self._remove_temp_file(old_preview)
            self.tts_finished_signal.emit("Đã tạo preview thành công.", True)
        except asyncio.CancelledError:
            self.tts_finished_signal.emit("Đã dừng xử lý theo yêu cầu người dùng.", False)
        except Exception as exc:
            self.tts_finished_signal.emit(f"Lỗi preview: {exc}", False)

    def run_tts_text(self, text, provider_id, voice_id, speed, save_path):
        try:
            tmp_path = self._run_tracked_async(
                process_text_only(
                    text=text,
                    voice=voice_id,
                    provider_id=provider_id,
                    speed=speed,
                    progress_callback=lambda p, t: self.tts_progress_signal.emit(int(p * 100), t),
                    cancel_callback=lambda: self.tts_cancel_requested,
                ),
                "tts",
            )
            if self.tts_cancel_requested:
                raise RuntimeError("Đã dừng xử lý theo yêu cầu người dùng.")
            os.replace(tmp_path, save_path)
            self.tts_finished_signal.emit(f"Đã lưu file {os.path.basename(save_path)}.", True)
        except asyncio.CancelledError:
            self.tts_finished_signal.emit("Đã dừng xử lý theo yêu cầu người dùng.", False)
        except Exception as exc:
            self.tts_finished_signal.emit(f"Lỗi: {exc}", False)

    def run_tts_srt(self, content, provider_id, voice_id, speed, save_path):
        try:
            subs = parse_srt(content)
            tmp_path = self._run_tracked_async(
                process_srt_logic(
                    srt_blocks=subs,
                    voice=voice_id,
                    provider_id=provider_id,
                    speed=speed,
                    progress_callback=lambda p, t: self.tts_progress_signal.emit(int(p * 100), t),
                    cancel_callback=lambda: self.tts_cancel_requested,
                ),
                "tts",
            )
            if self.tts_cancel_requested:
                raise RuntimeError("Đã dừng xử lý theo yêu cầu người dùng.")
            os.replace(tmp_path, save_path)
            self.tts_finished_signal.emit(f"Đã lưu file {os.path.basename(save_path)}.", True)
        except asyncio.CancelledError:
            self.tts_finished_signal.emit("Đã dừng xử lý theo yêu cầu người dùng.", False)
        except Exception as exc:
            self.tts_finished_signal.emit(f"Lỗi: {exc}", False)

    def handle_translate_text(self):
        text = self.translator_textbox.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập văn bản cần dịch!")
            return
        try:
            save_path = self._resolve_output_path(getattr(self, "translator_output_dir", ""), build_timestamp_name("translated_output", "txt"))
        except Exception as exc:
            QMessageBox.warning(self, "Cảnh báo", str(exc))
            return
        self.start_background(lambda: self.run_translate_text(text, save_path), "translator")

    def handle_translate_srt(self):
        if not self.translator_srt_content:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn file SRT!")
            return
        try:
            save_path = self._resolve_output_path(getattr(self, "translator_output_dir", ""), build_timestamp_name("translated_output", "srt"))
        except Exception as exc:
            QMessageBox.warning(self, "Cảnh báo", str(exc))
            return
        self.start_background(lambda: self.run_translate_srt(self.translator_srt_content, save_path), "translator")

    def handle_resume_translation(self):
        resume = self.state.get("translator", {}).get("resume", {})
        if not resume.get("job_hash"):
            QMessageBox.information(self, "Thông báo", "Không có bản dịch dở nào để tiếp tục.")
            return
        if resume.get("input_type") == "text":
            self.handle_translate_text()
        elif resume.get("input_type") == "srt":
            self.handle_translate_srt()

    def _translator_checkpoint(self, job_hash, input_type, source_lang, target_lang, translated_items):
        self.state.setdefault("translator", {})["resume"] = build_resume_payload(
            job_hash=job_hash,
            input_type=input_type,
            source_lang=source_lang,
            target_lang=target_lang,
            translated_items={int(key): value for key, value in translated_items.items()},
        )
        save_state(self.build_state_from_ui())

    def run_translate_text(self, text, save_path):
        llm_provider = self.translator_provider_combo.currentData() or "deepseek"
        source_lang = self.source_lang_combo.currentData() or "auto"
        target_lang = self.target_lang_combo.currentData() or "vi"
        model = self.translator_model_combo.currentText()
        api_key = self._current_translator_api_key()
        items = translate_plain_text_to_items(text)
        job_hash = build_job_hash("text", text, source_lang, target_lang)
        resume = self.state.get("translator", {}).get("resume", {})
        existing = parse_resume_payload(resume) if resume.get("job_hash") == job_hash else {}
        try:
            translations = self._run_tracked_async(
                translate_items(
                    items=items,
                    llm_provider=llm_provider,
                    api_key=api_key,
                    model=model,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    existing_translations=existing,
                    progress_callback=lambda p, t: self.translator_progress_signal.emit(int(p * 100), t),
                    checkpoint_callback=lambda data: self._translator_checkpoint(job_hash, "text", source_lang, target_lang, data),
                    cancel_callback=lambda: self.translator_cancel_requested,
                ),
                "translator",
            )
            if self.translator_cancel_requested:
                raise RuntimeError("Đã dừng dịch theo yêu cầu người dùng.")
            output_text = render_plain_text(items, translations)
            with open(save_path, "w", encoding="utf-8") as handle:
                handle.write(output_text)
            self.state.setdefault("translator", {})["resume"] = build_resume_payload(
                job_hash, "text", source_lang, target_lang, translations, "", save_path
            )
            save_state(self.build_state_from_ui())
            self.translator_finished_signal.emit(f"Đã lưu file {os.path.basename(save_path)}.", True)
        except asyncio.CancelledError:
            self.translator_finished_signal.emit("Đã dừng dịch theo yêu cầu người dùng.", False)
        except Exception as exc:
            self.state.setdefault("translator", {})["resume"] = build_resume_payload(
                job_hash, "text", source_lang, target_lang, existing, str(exc), ""
            )
            save_state(self.build_state_from_ui())
            self.translator_finished_signal.emit(f"Lỗi dịch văn bản: {exc}", False)

    def run_translate_srt(self, content, save_path):
        llm_provider = self.translator_provider_combo.currentData() or "deepseek"
        source_lang = self.source_lang_combo.currentData() or "auto"
        target_lang = self.target_lang_combo.currentData() or "vi"
        model = self.translator_model_combo.currentText()
        api_key = self._current_translator_api_key()
        subs = parse_srt(content)
        items = build_srt_items(subs)
        job_hash = build_job_hash("srt", content, source_lang, target_lang)
        resume = self.state.get("translator", {}).get("resume", {})
        existing = parse_resume_payload(resume) if resume.get("job_hash") == job_hash else {}
        try:
            translations = self._run_tracked_async(
                translate_items(
                    items=items,
                    llm_provider=llm_provider,
                    api_key=api_key,
                    model=model,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    existing_translations=existing,
                    progress_callback=lambda p, t: self.translator_progress_signal.emit(int(p * 100), t),
                    checkpoint_callback=lambda data: self._translator_checkpoint(job_hash, "srt", source_lang, target_lang, data),
                    cancel_callback=lambda: self.translator_cancel_requested,
                ),
                "translator",
            )
            if self.translator_cancel_requested:
                raise RuntimeError("Đã dừng dịch theo yêu cầu người dùng.")
            translated_subs = apply_translations_to_subs(subs, translations)
            with open(save_path, "w", encoding="utf-8") as handle:
                handle.write(serialize_srt(translated_subs))
            self.state.setdefault("translator", {})["resume"] = build_resume_payload(
                job_hash, "srt", source_lang, target_lang, translations, "", save_path
            )
            save_state(self.build_state_from_ui())
            self.translator_finished_signal.emit(f"Đã lưu file {os.path.basename(save_path)}.", True)
        except asyncio.CancelledError:
            self.translator_finished_signal.emit("Đã dừng dịch theo yêu cầu người dùng.", False)
        except Exception as exc:
            self.state.setdefault("translator", {})["resume"] = build_resume_payload(
                job_hash, "srt", source_lang, target_lang, existing, str(exc), ""
            )
            save_state(self.build_state_from_ui())
            self.translator_finished_signal.emit(f"Lỗi dịch SRT: {exc}", False)

    def start_background(self, target, mode: str):
        if mode == "tts":
            self.tts_cancel_requested = False
            self.btn_gen_txt.setEnabled(False)
            self.btn_gen_srt.setEnabled(False)
            self.btn_preview.setEnabled(False)
            self.btn_stop_tts.setEnabled(True)
            self.tts_progress_bar.setValue(0)
        else:
            self.translator_cancel_requested = False
            self.btn_translate_text.setEnabled(False)
            self.btn_translate_srt.setEnabled(False)
            self.btn_resume_translation.setEnabled(False)
            self.btn_stop_translation.setEnabled(True)
            self.translator_progress_bar.setValue(0)
        threading.Thread(target=target, daemon=True).start()

    def _current_translator_api_key(self) -> str:
        provider = self.translator_provider_combo.currentData() or "deepseek"
        if provider == "google":
            return self.translator_google_api_key.text().strip()
        return self.translator_deepseek_api_key.text().strip()

    def _update_tts_progress_ui(self, value, text):
        self.tts_progress_bar.setValue(value)
        self.tts_progress_label.setText(text)
        self.append_tts_log(text)

    def _tts_finished_ui(self, message, success):
        self.refresh_provider_ui()
        self.btn_preview.setEnabled(self.voice_combo.count() > 0)
        self.btn_stop_tts.setEnabled(False)
        self.tts_cancel_requested = False
        self.append_tts_log(message)
        if success:
            QMessageBox.information(self, "Hoàn tất", message)
        else:
            QMessageBox.critical(self, "Lỗi", message)

    def _update_translator_progress_ui(self, value, text):
        self.translator_progress_bar.setValue(value)
        self.translator_progress_label.setText(text)
        self.append_translator_log(text)

    def _translator_finished_ui(self, message, success):
        self.btn_translate_text.setEnabled(True)
        self.btn_translate_srt.setEnabled(True)
        self.btn_resume_translation.setEnabled(True)
        self.btn_stop_translation.setEnabled(False)
        self.translator_cancel_requested = False
        self.append_translator_log(message)
        if success:
            QMessageBox.information(self, "Hoàn tất", message)
        else:
            QMessageBox.critical(self, "Lỗi", message)

    def closeEvent(self, event):
        self._remove_temp_file(self.current_preview_path)
        self.current_preview_path = None
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())
