"""Programmatic Qt dialog for the standalone QGIS plugin."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from qgis.PyQt.QtCore import Qt, QProcess, QProcessEnvironment, QSettings
from qgis.PyQt.QtGui import QColor, QDesktopServices, QFont, QFontDatabase
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QDoubleSpinBox,
    QSpinBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsSingleSymbolRenderer,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)

from .task_bridge import RESULT_PREFIX, default_worker_python
from .delivery_exporter import RESULT_PREFIX as EXPORT_RESULT_PREFIX
from .model_client import ModelClient
from .agent_plan import parse_plan_response
from .agent_prompts import DIAGNOSIS_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT
from .input_config_builder import BUSINESS_LAYERS, build_task1_config


REFERENCE_LAYER_STYLES = {
    "IMB": {"kind": "point", "shape": "square", "color": "#000000", "size": 2.4, "outline_color": "#FFFFFF", "outline_width": 0.4, "label": "CODE", "label_color": "#323232"},
    "SITE": {"kind": "point", "shape": "circle", "color": "#A47158", "size": 2.0, "outline_color": "#232323", "outline_width": 0.0},
    "BOITE": {"kind": "point", "shape": "circle", "color": "#E15989", "size": 2.0, "outline_color": "#232323", "outline_width": 0.0, "label": "CODE", "label_color": "#E73232"},
    "PTECH": {"kind": "point", "shape": "diamond", "color": "#DB1E2A", "size": 4.4, "outline_color": "#801119", "outline_width": 0.4, "label": "CODE", "label_color": "#323232"},
    "INFRASTRUCTURE": {"kind": "line", "color": "#E71340", "width": 0.66},
    "ZNRO": {"kind": "polygon", "color": "#BEB297", "outline_color": "#232323", "outline_width": 0.26},
    "ZPM": {"kind": "polygon", "color": "#E5B636", "outline_color": "#232323", "outline_width": 0.26},
    "CABLE": {"kind": "cable"},
}

AUXILIARY_LAYER_COLORS = {
    "GEO_BUILDING": "#C47A34",
    "GEO_ROAD": "#8A8A8A",
    "GEO_ROAD_NODE": "#6B7280",
    "GEO_BARRIER": "#C62828",
    "GEO_COST_ZONE": "#EF6C00",
    "GEO_ACCESS_CANDIDATE": "#8E24AA",
}


def _chinese_ui_font(point_size: int = 9) -> QFont:
    """Load a bundled-system CJK font when Qt's offscreen font scan is empty."""
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    for path in candidates:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            return QFont(families[0], point_size)
    return QFont("sans-serif", point_size)


class FtthDataContractDialog(QDialog):
    def __init__(self, iface, plugin_dir: Path):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.plugin_dir = Path(plugin_dir)
        self.engine_dir = self.plugin_dir / "engine"
        self.last_result: dict[str, Any] | None = None
        self._bridge_process = None
        self._bridge_output = bytearray()
        self._export_process = None
        self._export_output = bytearray()
        self._pending_agent_plan = None
        self._model_requests = {}
        self.settings = QSettings()
        self.model_client = ModelClient(self)
        self.model_client.completed.connect(self._model_completed)
        self.model_client.failed.connect(self._model_failed)
        self.setFont(_chinese_ui_font())
        self.setWindowTitle("FTTH 连续规划智能体 2.4.2")
        self.setMinimumSize(450, 590)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        title = QLabel("FTTH 智能规划与成果导出")
        title.setStyleSheet("font-size: 20px; font-weight: 600; color: #17324d; padding: 4px 0;")
        subtitle = QLabel("数据治理 · AI参数辅助 · 连续确定性规划 · 完整成果发布")
        subtitle.setStyleSheet("color: #5d6b78; padding-bottom: 8px;")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        project_ai = QTabWidget()
        project_ai.addTab(self._base_tab(), "数据输入")
        project_ai.addTab(self._agent_tab(), "AI辅助")
        self.tabs.addTab(project_ai, "① 项目与AI")
        self.tabs.addTab(self._algorithm_tab(), "② 环境与连续规划")
        self.tabs.addTab(self._delivery_tab(), "③ 成果与校验")
        feedback_panel = QWidget()
        feedback_layout = QVBoxLayout(feedback_panel)
        feedback_layout.setContentsMargins(0, 4, 0, 0)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("font-weight: 600; color: #2d6a4f;")
        feedback_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("就绪")
        feedback_layout.addWidget(self.progress_bar)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("运行结果和校验问题会显示在这里。")
        self.log.setMinimumHeight(70)
        feedback_layout.addWidget(self.log, 1)

        bottom = QHBoxLayout()
        self.open_report_button = QPushButton("打开最近报告")
        self.open_report_button.setEnabled(False)
        self.open_report_button.clicked.connect(self._open_report)
        close_button = QPushButton("隐藏侧栏")
        close_button.clicked.connect(self._hide_panel)
        bottom.addWidget(self.open_report_button)
        bottom.addStretch(1)
        bottom.addWidget(close_button)
        feedback_layout.addLayout(bottom)

        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(7)
        self.main_splitter.setStyleSheet("QSplitter::handle:vertical { background: #d7e0e8; margin: 2px 0; }")
        self.main_splitter.addWidget(self.tabs)
        self.main_splitter.addWidget(feedback_panel)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([390, 210])
        root.addWidget(self.main_splitter, 1)

    def _hide_panel(self):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QDockWidget):
                parent.hide()
                return
            parent = parent.parentWidget()
        self.close()

    def _base_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("IMB 是唯一必填的原始需求图层；SITE、PTECH、INFRASTRUCTURE 是可选现状约束。可直接选择 CSV、XLS/XLSX、SHP、GeoJSON 或 GPKG，原始数据不会被改写。")
        note.setWordWrap(True)
        layout.addWidget(note)
        quick_form = QFormLayout()
        self.quick_sources = {}
        self.quick_sublayers = {}
        for name in BUSINESS_LAYERS:
            source_edit = QLineEdit()
            source_edit.setPlaceholderText(
                "选择 IMB 建筑需求数据" if name == "IMB"
                else f"可选：选择已有 {name} 现状数据"
            )
            sublayer_edit = QLineEdit()
            sublayer_edit.setFixedWidth(120)
            sublayer_edit.setPlaceholderText("Sheet/图层名")
            browse_source = QPushButton("选择…")
            browse_source.clicked.connect(lambda _checked=False, value=name: self._browse_quick_source(value))
            row = QHBoxLayout()
            row.addWidget(source_edit)
            row.addWidget(sublayer_edit)
            row.addWidget(browse_source)
            quick_form.addRow("IMB（必填）" if name == "IMB" else f"{name}（选填）", row)
            self.quick_sources[name] = source_edit
            self.quick_sublayers[name] = sublayer_edit
        self.input_crs = QLineEdit("EPSG:4326")
        quick_form.addRow("输入坐标系", self.input_crs)
        self.generated_config_path = QLineEdit()
        self.generated_config_path.setPlaceholderText("默认保存到第一个输入文件旁的 ftth_task1_config.json")
        choose_config_output = QPushButton("另存为…")
        choose_config_output.clicked.connect(self._browse_generated_config)
        config_output_row = QHBoxLayout()
        config_output_row.addWidget(self.generated_config_path)
        config_output_row.addWidget(choose_config_output)
        quick_form.addRow("生成配置位置", config_output_row)
        layout.addLayout(quick_form)
        quick_buttons = QHBoxLayout()
        generate = QPushButton("生成配置并校验")
        generate.clicked.connect(self.generate_input_config)
        generate_run = QPushButton("生成配置并构建图层")
        generate_run.setStyleSheet("font-weight: 600; padding: 7px 14px;")
        generate_run.clicked.connect(lambda: self.generate_input_config(run_after=True))
        quick_buttons.addWidget(generate)
        quick_buttons.addWidget(generate_run)
        quick_buttons.addStretch(1)
        layout.addLayout(quick_buttons)
        quick_hint = QLabel("仅提供 IMB 时可构建基础成果并运行任务二分区；未提供的三类现状数据会成为空占位层。任务三至六涉及选址和物理路由，仍需 PTECH+INFRASTRUCTURE，或人工确认的道路/识别网络。")
        quick_hint.setWordWrap(True)
        layout.addWidget(quick_hint)
        self.input_readiness_status = QLabel("等待选择 IMB。")
        self.input_readiness_status.setWordWrap(True)
        layout.addWidget(self.input_readiness_status)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        layout.addWidget(QLabel("高级方式：使用已有任务一配置 JSON"))
        form = QFormLayout()
        self.config_path = QLineEdit(str(self.plugin_dir / "task1_config.json"))
        browse = QPushButton("选择…")
        browse.clicked.connect(self._browse_config)
        row = QHBoxLayout()
        row.addWidget(self.config_path)
        row.addWidget(browse)
        form.addRow("任务一配置", row)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        validate = QPushButton("仅校验数据")
        validate.clicked.connect(self.validate_base)
        build = QPushButton("生成并加载图层")
        build.setDefault(True)
        build.setStyleSheet("font-weight: 600; padding: 7px 14px;")
        build.clicked.connect(self.build_base)
        buttons.addWidget(validate)
        buttons.addWidget(build)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _algorithm_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("使用①页配置重新建图。识别模型在插件外运行；这里仅接收已经生成的本区域识别结果。任务二至六严格读取本批次成果并按新算法规定的顺序连续执行。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.bridge_config = QLineEdit(self.config_path.text())
        self.bridge_config.setReadOnly(True)
        self.config_path.textChanged.connect(self.bridge_config.setText)
        form.addRow("本次任务一配置", self.bridge_config)
        self.worker_python = QLineEdit(default_worker_python())
        browse_python = QPushButton("选择…")
        browse_python.clicked.connect(self._browse_worker_python)
        python_row = QHBoxLayout()
        python_row.addWidget(self.worker_python)
        python_row.addWidget(browse_python)
        form.addRow("QGIS Python", python_row)
        self.recognition_gpkg = QLineEdit()
        self.recognition_gpkg.setPlaceholderText("选择 regional_geo_context.gpkg；留空则只使用正式现状数据")
        browse_gpkg = QPushButton("选择…")
        browse_gpkg.clicked.connect(self._browse_recognition_gpkg)
        gpkg_row = QHBoxLayout()
        gpkg_row.addWidget(self.recognition_gpkg)
        gpkg_row.addWidget(browse_gpkg)
        form.addRow("识别结果 GPKG（选填）", gpkg_row)
        self.bridge_output_root = QLineEdit()
        self.bridge_output_root.setPlaceholderText("留空：配置目录 / outputs / connected_runs，每次独立新目录")
        browse_output = QPushButton("选择…")
        browse_output.clicked.connect(self._browse_bridge_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.bridge_output_root)
        output_row.addWidget(browse_output)
        form.addRow("连接成果目录", output_row)
        layout.addLayout(form)
        self.run_task3 = QCheckBox("继续运行任务三设施选址")
        self.run_task3.setChecked(True)
        layout.addWidget(self.run_task3)
        self.run_downstream = QCheckBox("连续运行任务四初算 → 任务三优化 → 任务四定稿 → 任务六 → 任务五")
        self.run_downstream.setChecked(True)
        layout.addWidget(self.run_downstream)
        self.ga_refine = QCheckBox("启用任务三 GA 路由反馈优化")
        self.ga_refine.setChecked(True)
        layout.addWidget(self.ga_refine)
        self.split_oversized = QCheckBox("确认：超过48端口的IMB拆为多个服务单元，并保留原楼栋映射")
        layout.addWidget(self.split_oversized)
        self.use_formal_infrastructure = QCheckBox("自动使用本批次输入的INFRASTRUCTURE（有数据时强制纳入）")
        layout.addWidget(self.use_formal_infrastructure)
        self.use_recognition_roads = QCheckBox("确认：识别道路作为待复核的新建候选路径")
        layout.addWidget(self.use_recognition_roads)

        self.standard_parameters = QCheckBox("使用算法默认规划参数（推荐）")
        self.standard_parameters.setChecked(True)
        layout.addWidget(self.standard_parameters)
        fixed_note = QLabel(
            "标准固定值：NRO 2048、ZPM 576、空间/管网权重 1/100、PBO偏移 5m、候选路径 5、"
            "水域系数 2、PTECH间距 200m、吸附距离 80m、GA 14×10。只有输入数据、输出目录、"
            "道路来源确认，以及正式管道缺少容量字段时的容量假设需要人工填写。"
        )
        fixed_note.setWordWrap(True)
        layout.addWidget(fixed_note)
        downstream_form = QFormLayout()
        self.nro_max_cap = QSpinBox()
        self.nro_max_cap.setRange(1, 1000000)
        self.nro_max_cap.setValue(2048)
        downstream_form.addRow("NRO最大容量", self.nro_max_cap)
        self.zpm_max_cap = QSpinBox()
        self.zpm_max_cap.setRange(1, 1000000)
        self.zpm_max_cap.setValue(576)
        downstream_form.addRow("ZPM最大容量", self.zpm_max_cap)
        self.spatial_weight = QDoubleSpinBox()
        self.spatial_weight.setRange(0.0, 10000.0)
        self.spatial_weight.setValue(1.0)
        downstream_form.addRow("空间距离权重", self.spatial_weight)
        self.pipe_weight = QDoubleSpinBox()
        self.pipe_weight.setRange(0.0, 10000.0)
        self.pipe_weight.setValue(100.0)
        downstream_form.addRow("现有管网权重", self.pipe_weight)
        self.formal_duct_capacity = QSpinBox()
        self.formal_duct_capacity.setRange(1, 1000000)
        self.formal_duct_capacity.setValue(2048)
        downstream_form.addRow("确认管道容量", self.formal_duct_capacity)
        self.pbo_offset = QDoubleSpinBox()
        self.pbo_offset.setRange(0.1, 1000.0)
        self.pbo_offset.setValue(5.0)
        self.pbo_offset.setSuffix(" m")
        downstream_form.addRow("PBO离建筑距离", self.pbo_offset)
        self.candidate_paths = QSpinBox()
        self.candidate_paths.setRange(1, 20)
        self.candidate_paths.setValue(5)
        downstream_form.addRow("候选路径数", self.candidate_paths)
        self.water_cost_factor = QDoubleSpinBox()
        self.water_cost_factor.setRange(0.0, 1000.0)
        self.water_cost_factor.setValue(2.0)
        downstream_form.addRow("水域成本系数", self.water_cost_factor)
        self.ptech_spacing = QDoubleSpinBox()
        self.ptech_spacing.setRange(1.0, 100000.0)
        self.ptech_spacing.setValue(200.0)
        self.ptech_spacing.setSuffix(" m")
        downstream_form.addRow("PTECH最大间距", self.ptech_spacing)
        self.snap_distance = QDoubleSpinBox()
        self.snap_distance.setRange(1.0, 100000.0)
        self.snap_distance.setValue(80.0)
        self.snap_distance.setSuffix(" m")
        downstream_form.addRow("设施吸附距离", self.snap_distance)
        self.ga_population = QSpinBox()
        self.ga_population.setRange(4, 100)
        self.ga_population.setValue(14)
        downstream_form.addRow("GA种群数量", self.ga_population)
        self.ga_generations = QSpinBox()
        self.ga_generations.setRange(1, 100)
        self.ga_generations.setValue(10)
        downstream_form.addRow("GA迭代代数", self.ga_generations)
        layout.addLayout(downstream_form)
        self._standard_parameter_controls = (
            self.nro_max_cap, self.zpm_max_cap, self.spatial_weight, self.pipe_weight,
            self.pbo_offset, self.candidate_paths, self.water_cost_factor,
            self.ptech_spacing, self.snap_distance, self.ga_population,
            self.ga_generations, self.ga_refine,
        )
        self.standard_parameters.toggled.connect(self._toggle_standard_parameters)
        self._toggle_standard_parameters(True)
        self.bridge_button = QPushButton("生成本批次并运行已选算法")
        self.bridge_button.setStyleSheet("font-weight: 600; padding: 7px 14px;")
        self.bridge_button.clicked.connect(self.start_bridge)
        layout.addWidget(self.bridge_button)
        safety = QLabel("识别结果须为含 GEO_ROAD 等辅助图层的同区域 GeoPackage。插件不再运行影像识别，也不要求模型、CUDA 或独立 AI Python。界面容量是本批次规划假设，不代表现场管孔或施工许可；全部成功后才发布，失败不覆盖旧成果。")
        safety.setWordWrap(True)
        layout.addWidget(safety)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _agent_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("AI只负责把自然语言转换为白名单参数，并解释本批次报告；确定性控制器仍负责运行任务一至六。AI不能替用户确认施工条件。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.model_provider = QComboBox()
        self.model_provider.addItems(["OpenAI兼容", "DeepSeek", "通义千问"])
        self.model_provider.currentTextChanged.connect(self._provider_changed)
        form.addRow("模型服务", self.model_provider)
        self.model_base_url = QLineEdit(str(self.settings.value("ftth_ai/base_url", "https://api.openai.com/v1")))
        form.addRow("API地址", self.model_base_url)
        self.model_name = QLineEdit(str(self.settings.value("ftth_ai/model", "gpt-4.1-mini")))
        form.addRow("模型ID", self.model_name)
        self.model_api_key = QLineEdit()
        self.model_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_api_key.setPlaceholderText("仅保存在当前QGIS会话，不写入项目或插件ZIP")
        form.addRow("API Key", self.model_api_key)
        self.model_temperature = QDoubleSpinBox()
        self.model_temperature.setRange(0.0, 2.0)
        self.model_temperature.setSingleStep(0.1)
        self.model_temperature.setValue(float(self.settings.value("ftth_ai/temperature", 0.1)))
        form.addRow("Temperature", self.model_temperature)
        self.model_timeout = QSpinBox()
        self.model_timeout.setRange(5, 300)
        self.model_timeout.setValue(int(self.settings.value("ftth_ai/timeout", 60)))
        self.model_timeout.setSuffix(" 秒")
        form.addRow("请求超时", self.model_timeout)
        layout.addLayout(form)
        self.ai_content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.ai_content_splitter.setChildrenCollapsible(False)
        self.ai_content_splitter.setHandleWidth(7)
        self.ai_content_splitter.setStyleSheet("QSplitter::handle:vertical { background: #d7e0e8; margin: 2px 0; }")
        request_panel = QWidget()
        request_layout = QVBoxLayout(request_panel)
        request_layout.setContentsMargins(0, 2, 0, 2)
        self.agent_requirement = QTextEdit()
        self.agent_requirement.setPlaceholderText("例如：使用当前项目完成完整规划，确认路由容量2048，PBO离建筑5米，PTECH最大间距200米，并尽量避开高成本区域。")
        self.agent_requirement.setMinimumHeight(50)
        request_layout.addWidget(QLabel("规划需求（自然语言）"))
        request_layout.addWidget(self.agent_requirement, 1)
        buttons = QHBoxLayout()
        test = QPushButton("测试模型")
        test.clicked.connect(self._test_model)
        parse = QPushButton("解析规划需求")
        parse.clicked.connect(self._request_plan)
        diagnose = QPushButton("诊断最近运行")
        diagnose.clicked.connect(self._request_diagnosis)
        buttons.addWidget(test)
        buttons.addWidget(parse)
        buttons.addWidget(diagnose)
        request_layout.addLayout(buttons)
        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(0, 2, 0, 2)
        self.agent_output = QTextEdit()
        self.agent_output.setReadOnly(True)
        self.agent_output.setMinimumHeight(70)
        self.agent_output.setPlaceholderText("模型返回、结构化参数、缺失信息和运行诊断显示在这里。")
        result_layout.addWidget(self.agent_output, 1)
        apply_button = QPushButton("审核后应用数值参数")
        apply_button.setStyleSheet("font-weight: 600; padding: 7px 14px;")
        apply_button.clicked.connect(self._apply_agent_plan)
        result_layout.addWidget(apply_button)
        warning = QLabel("默认参数模式下，AI不会改写算法参数；它只解析运行范围和需要人工确认的工程假设。关闭默认参数模式后，才允许审核并应用高级数值参数。输入的正式INFRASTRUCTURE会自动进入路由计算；识别道路仍须手工确认。")
        warning.setWordWrap(True)
        result_layout.addWidget(warning)
        self.ai_content_splitter.addWidget(request_panel)
        self.ai_content_splitter.addWidget(result_panel)
        self.ai_content_splitter.setStretchFactor(0, 1)
        self.ai_content_splitter.setStretchFactor(1, 2)
        self.ai_content_splitter.setSizes([110, 220])
        layout.addWidget(self.ai_content_splitter, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _model_settings(self):
        self.settings.setValue("ftth_ai/base_url", self.model_base_url.text().strip())
        self.settings.setValue("ftth_ai/model", self.model_name.text().strip())
        self.settings.setValue("ftth_ai/temperature", self.model_temperature.value())
        self.settings.setValue("ftth_ai/timeout", self.model_timeout.value())
        return {
            "base_url": self.model_base_url.text().strip(),
            "api_key": self.model_api_key.text().strip(),
            "model": self.model_name.text().strip(),
            "temperature": self.model_temperature.value(),
            "timeout_ms": self.model_timeout.value() * 1000,
        }

    def _provider_changed(self, provider):
        presets = {
            "OpenAI兼容": ("https://api.openai.com/v1", "gpt-4.1-mini"),
            "DeepSeek": ("https://api.deepseek.com", "deepseek-chat"),
            "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        }
        if provider in presets:
            base, model = presets[provider]
            self.model_base_url.setText(base)
            self.model_name.setText(model)

    def _agent_project_context(self):
        layer_names = []
        try:
            config = json.loads(Path(self.config_path.text().strip()).read_text(encoding="utf-8-sig"))
            layer_names = [str(item.get("name")) for item in config.get("vector_layers") or [] if item.get("name")]
        except Exception:
            pass
        return {
            "available_input_layers": layer_names,
            "available_sequence": ["task1", "task2", "task3_initial", "task4", "task6_baseline", "task3_ga_refinement", "task5"],
            "current_parameters": {
                "nro_max_cap": self.nro_max_cap.value(), "zpm_max_cap": self.zpm_max_cap.value(),
                "spatial_weight": self.spatial_weight.value(), "pipe_weight": self.pipe_weight.value(),
                "formal_duct_capacity": self.formal_duct_capacity.value(), "pbo_offset_m": self.pbo_offset.value(),
                "candidate_paths": self.candidate_paths.value(), "water_cost_factor": self.water_cost_factor.value(),
                "ptech_max_spacing_m": self.ptech_spacing.value(), "terminal_snap_distance_m": self.snap_distance.value(),
                "ga_population": self.ga_population.value(), "ga_generations": self.ga_generations.value(),
            },
        }

    def _send_model(self, mode, messages):
        try:
            request_id = self.model_client.chat(messages=messages, **self._model_settings())
            self._model_requests[request_id] = mode
            self.agent_output.setPlainText("模型请求中…")
            self.status_label.setText("AI正在处理…")
        except Exception as exc:
            self.agent_output.setPlainText(f"模型请求失败：{exc}")

    def _test_model(self):
        self._send_model("test", [{"role": "user", "content": "只回复：连接成功"}])

    def _request_plan(self):
        requirement = self.agent_requirement.toPlainText().strip()
        if not requirement:
            self.agent_output.setPlainText("请先输入自然语言规划需求。")
            return
        context = json.dumps(self._agent_project_context(), ensure_ascii=False)
        self._send_model("plan", [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": f"项目摘要：{context}\n用户需求：{requirement}"},
        ])

    def _request_diagnosis(self):
        if not self.last_result:
            self.agent_output.setPlainText("还没有可诊断的运行或导出结果。")
            return
        report = json.dumps(self.last_result, ensure_ascii=False)
        self._send_model("diagnosis", [
            {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
            {"role": "user", "content": report[:24000]},
        ])

    def _model_completed(self, request_id, content, _raw):
        mode = self._model_requests.pop(request_id, "")
        try:
            if mode == "plan":
                self._pending_agent_plan = parse_plan_response(content)
                self.agent_output.setPlainText(json.dumps(self._pending_agent_plan, ensure_ascii=False, indent=2))
                self.status_label.setText("AI参数已解析，请审核后应用")
            else:
                self.agent_output.setPlainText(content)
                self.status_label.setText("AI请求完成")
        except Exception as exc:
            self._pending_agent_plan = None
            self.agent_output.setPlainText(f"模型回复未通过白名单校验：{exc}\n\n原始回复：\n{content}")
            self.status_label.setText("AI回复校验失败")

    def _model_failed(self, request_id, error):
        self._model_requests.pop(request_id, None)
        self.agent_output.setPlainText(error)
        self.status_label.setText("AI请求失败；仍可直接使用表单运行")

    def _apply_agent_plan(self):
        if not self._pending_agent_plan:
            self.agent_output.append("\n没有通过校验的AI方案可应用。")
            return
        controls = {
            "nro_max_cap": self.nro_max_cap, "zpm_max_cap": self.zpm_max_cap,
            "spatial_weight": self.spatial_weight, "pipe_weight": self.pipe_weight,
            "formal_duct_capacity": self.formal_duct_capacity, "pbo_offset_m": self.pbo_offset,
            "candidate_paths": self.candidate_paths, "water_cost_factor": self.water_cost_factor,
            "ptech_max_spacing_m": self.ptech_spacing, "terminal_snap_distance_m": self.snap_distance,
            "ga_population": self.ga_population, "ga_generations": self.ga_generations,
        }
        fixed_names = {
            "nro_max_cap", "zpm_max_cap", "spatial_weight", "pipe_weight",
            "pbo_offset_m", "candidate_paths", "water_cost_factor",
            "ptech_max_spacing_m", "terminal_snap_distance_m",
            "ga_population", "ga_generations",
        }
        skipped = []
        for name, value in self._pending_agent_plan["parameters"].items():
            if value is not None and name in controls:
                if self.standard_parameters.isChecked() and name in fixed_names:
                    skipped.append(name)
                    continue
                controls[name].setValue(value)
        scope = self._pending_agent_plan.get("run_scope")
        self.run_task3.setChecked(scope in {"full", "through_task3"})
        self.run_downstream.setChecked(scope == "full")
        self.tabs.setCurrentIndex(1)
        self.status_label.setText(
            "AI方案已应用；标准固定参数未改写" if skipped
            else "AI数值参数已应用；请手工确认业务条件后运行"
        )

    def _toggle_standard_parameters(self, checked):
        defaults = {
            self.nro_max_cap: 2048,
            self.zpm_max_cap: 576,
            self.spatial_weight: 1.0,
            self.pipe_weight: 100.0,
            self.pbo_offset: 5.0,
            self.candidate_paths: 5,
            self.water_cost_factor: 2.0,
            self.ptech_spacing: 200.0,
            self.snap_distance: 80.0,
            self.ga_population: 14,
            self.ga_generations: 10,
        }
        if checked:
            for control, value in defaults.items():
                control.setValue(value)
            self.ga_refine.setChecked(True)
        for control in self._standard_parameter_controls:
            control.setEnabled(not checked)

    def _browse_worker_python(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 QGIS 对应的 Python 解释器", self.worker_python.text())
        if path:
            self.worker_python.setText(path)

    def _browse_bridge_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择连接成果目录", self.bridge_output_root.text())
        if path:
            self.bridge_output_root.setText(path)

    def _browse_recognition_gpkg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择本区域识别 GeoPackage", self.recognition_gpkg.text(), "GeoPackage (*.gpkg)"
        )
        if path:
            self.recognition_gpkg.setText(path)

    def start_bridge(self) -> bool:
        if self._bridge_process is not None or self._export_process is not None:
            return False
        self._set_running("正在运行本批次任务一 → 识别结果接入（可选）→ 任务二至六连续规划…")
        self.tabs.setEnabled(False)
        try:
            config = Path(self.config_path.text().strip()).expanduser().resolve(strict=True)
            python = Path(self.worker_python.text().strip()).expanduser().resolve(strict=True)
            if not python.is_file() or not python.name.lower().startswith("python"):
                raise ValueError("请选择 QGIS 对应的 python.exe，不是 qgis-bin.exe 或启动批处理。")
            arguments = ["-u", str(self.plugin_dir / "task_bridge.py"), "--config", str(config)]
            arguments += [
                "--nro-max-cap", str(self.nro_max_cap.value()),
                "--zpm-max-cap", str(self.zpm_max_cap.value()),
                "--spatial-weight", str(self.spatial_weight.value()),
                "--pipe-weight", str(self.pipe_weight.value()),
            ]
            recognition_gpkg = self.recognition_gpkg.text().strip()
            if recognition_gpkg:
                arguments += ["--recognition-gpkg", str(Path(recognition_gpkg).expanduser().resolve(strict=True))]
            output = self.bridge_output_root.text().strip()
            if output:
                arguments += ["--output-root", str(Path(output).expanduser().resolve())]
            if self.run_task3.isChecked():
                arguments.append("--run-task3")
            if self.run_downstream.isChecked():
                if not self.run_task3.isChecked():
                    raise ValueError("运行任务四至六前必须勾选任务三。")
                if not self.split_oversized.isChecked():
                    raise ValueError("请确认超过48端口IMB的服务单元拆分规则。")
                arguments += [
                    "--run-tasks4-to6",
                    "--split-oversized-imbs",
                    "--formal-duct-capacity", str(self.formal_duct_capacity.value()),
                    "--pbo-offset-m", str(self.pbo_offset.value()),
                    "--candidate-paths", str(self.candidate_paths.value()),
                    "--water-cost-factor", str(self.water_cost_factor.value()),
                    "--ptech-max-spacing-m", str(self.ptech_spacing.value()),
                    "--terminal-snap-distance-m", str(self.snap_distance.value()),
                    "--ga-population", str(self.ga_population.value()),
                    "--ga-generations", str(self.ga_generations.value()),
                ]
                if not self.ga_refine.isChecked():
                    arguments.append("--no-ga-refine")
                if self.use_formal_infrastructure.isChecked():
                    arguments.append("--use-formal-infrastructure")
                if self.use_recognition_roads.isChecked():
                    arguments.append("--use-recognition-roads")
            process = QProcess(self)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            environment = QProcessEnvironment.systemEnvironment()
            prefix = QgsApplication.prefixPath()
            environment.insert("QGIS_PREFIX_PATH", prefix)
            environment.insert("PYTHONPATH", os.pathsep.join([str(Path(prefix) / "python"), environment.value("PYTHONPATH")]))
            environment.insert("PYTHONIOENCODING", "utf-8")
            environment.insert("PYTHONUTF8", "1")
            environment.insert("PYTHONDONTWRITEBYTECODE", "1")
            environment.insert("QT_QPA_PLATFORM", "offscreen")
            environment.insert("MPLBACKEND", "Agg")
            process.setProcessEnvironment(environment)
            process.setWorkingDirectory(str(config.parent))
            process.readyReadStandardOutput.connect(self._read_bridge_output)
            process.finished.connect(self._bridge_finished)
            process.errorOccurred.connect(self._bridge_error)
            self._bridge_output = bytearray()
            self.log.clear()
            self._bridge_process = process
            process.start(str(python), arguments)
            return True
        except Exception as exc:
            self.tabs.setEnabled(True)
            self._show_json({"status": "failed", "errors": [str(exc)]})
            self._finish(False, f"连接启动失败：{exc}", {"status": "failed", "errors": [str(exc)]})
            return False

    def _read_bridge_output(self):
        if self._bridge_process is None:
            return
        self._bridge_output.extend(bytes(self._bridge_process.readAllStandardOutput()))
        # Retain the final structured response while bounding GUI memory use.
        del self._bridge_output[:-1024 * 1024]
        output_text = self._bridge_output.decode("utf-8", errors="replace")
        self.log.setPlainText(output_text)
        stages = (
            ("生成本次基础图层", 12, "任务一：基础数据"),
            ("合成本次拓扑", 24, "接入识别结果"),
            ("clean_imb_topology_input.py", 34, "任务二：清理 IMB 输入"),
            ("znro_zpm_region_partition.py", 46, "任务二：服务区分区"),
            ("任务四初算", 58, "任务四：初始服务关系"),
            ("任务三：依据任务四", 70, "任务三：GA 优化 PM（耗时阶段）"),
            ("任务四定稿", 82, "任务四：锁定设施关系"),
            ("任务六：使用定稿", 90, "任务六：分层管线路由"),
            ("任务五：依据任务六", 96, "任务五：完整光缆拓扑"),
        )
        for marker, value, label in stages:
            if marker in output_text and value >= self.progress_bar.value():
                self.progress_bar.setValue(value)
                self.progress_bar.setFormat(f"{value}% · {label}")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _bridge_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._bridge_finished(-1, QProcess.ExitStatus.CrashExit)

    def _bridge_finished(self, exit_code, exit_status):
        process = self._bridge_process
        if process is None:
            return
        self._read_bridge_output()
        self._bridge_process = None
        self.tabs.setEnabled(True)
        result = {"status": "failed", "errors": [f"连接进程未返回成功响应（退出码 {exit_code}）：{process.errorString()}"]}
        try:
            for line in reversed(self._bridge_output.decode("utf-8", errors="replace").splitlines()):
                if line.startswith(RESULT_PREFIX):
                    result = json.loads(line[len(RESULT_PREFIX):])
                    break
            if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
                result["status"] = "failed"
                result.setdefault("errors", []).append(f"工作进程异常退出：{exit_code}")
            if result.get("status") == "success":
                self.export_run_path.setText(str(result.get("output_dir") or ""))
                if not self.export_output_root.text().strip() and result.get("output_dir"):
                    self.export_output_root.setText(str(Path(result["output_dir"]).parent / "deliveries"))
                try:
                    self._load_connected_layers(result)
                    self._zoom_to_project()
                except Exception as exc:
                    result.setdefault("warnings", []).append(f"连接成果已发布，但地图加载失败：{exc}")
                self._remember_report(result.get("report"))
                if result.get("release_status") == "READY_FOR_5_6_VALIDATION":
                    message = "所选算法已完成且下游校验通过，可进入完整成果校验"
                elif result.get("release_status") == "REVIEW_REQUIRED_BEFORE_5_6_VALIDATION":
                    message = "算法已执行，但任务六/五仍有待复核项，成果暂不生成"
                else:
                    message = "本批次已完成所选算法；规划候选仍需人工复核"
            else:
                staging = result.get("staging_dir")
                self._remember_report(str(Path(staging) / "failure_report.json") if staging else None)
                message = "连接失败；旧成果未覆盖，详情见下方错误"
            self._show_json(result)
            self._finish(result.get("status") == "success", message, result)
        except Exception as exc:
            result = {"status": "failed", "errors": [f"连接响应处理失败：{exc}"]}
            self._show_json(result)
            self._finish(False, result["errors"][0], result)
        finally:
            process.deleteLater()

    def _load_connected_layers(self, result):
        # Validate every layer before changing the project. Existing formal
        # service areas are never replaced by algorithm candidates.
        layers = []
        downstream = result.get("downstream") or {}
        final_dir = Path(str((downstream.get("final_layers") or {}).get("output_dir") or ""))
        if final_dir.is_dir():
            visibility = {
                "SITE": True, "BOITE": True, "PTECH": False, "IMB": True,
                "INFRASTRUCTURE": True, "CABLE": True, "ZPM": True, "ZNRO": False,
            }
            for name in ("ZNRO", "ZPM", "INFRASTRUCTURE", "CABLE", "PTECH", "SITE", "BOITE", "IMB"):
                layer = QgsVectorLayer(str(final_dir / f"{name}.shp"), name, "ogr")
                if not layer.isValid():
                    raise ValueError(f"无法加载最终规划图层：{name}")
                self._style(layer, name)
                layers.append((layer, name, visibility[name]))
            project = QgsProject.instance()
            group = project.layerTreeRoot().addGroup("FTTH 最终规划成果 " + Path(result["output_dir"]).name)
            for layer, _name, visible in layers:
                project.addMapLayer(layer, False)
                node = group.addLayer(layer)
                node.setItemVisibilityChecked(visible)
            self._add_reference_basemap(project, group)
            return
        for name in ("SITE", "PTECH", "IMB", "INFRASTRUCTURE"):
            layer = QgsVectorLayer(f"{result['geopackage']}|layername={name}", name, "ogr")
            layers.append((layer, name, "01 正式输入", name == "IMB"))
        for name, path in result["candidate_layers"].items():
            layer = QgsVectorLayer(path, f"{name}_规划候选_待复核", "ogr")
            layers.append((layer, name, "02 服务区规划", False))
        recognition = result.get("recognition") or {}
        recognition_gpkg = recognition.get("geopackage")
        if recognition_gpkg:
            for name in (
                "GEO_BUILDING", "GEO_ROAD", "GEO_ROAD_NODE",
                "GEO_BARRIER", "GEO_COST_ZONE", "GEO_ACCESS_CANDIDATE",
            ):
                layer = QgsVectorLayer(
                    f"{recognition_gpkg}|layername={name}", f"{name}_辅助候选", "ogr"
                )
                layers.append((layer, name, "03 环境感知辅助", False))
        for spec in downstream.get("layers") or []:
            path = str(spec.get("path") or "")
            display_name = str(spec.get("name") or Path(path).stem)
            layer = QgsVectorLayer(path, display_name + "_待复核", "ogr")
            upper_path = path.upper()
            style_key = (
                "BOITE" if "BOITE" in upper_path
                else "PTECH" if "PTECH" in upper_path
                else "SITE" if "SITE" in upper_path
                else "CABLE" if "FIBER" in upper_path or "CABLE" in upper_path
                else "INFRASTRUCTURE"
            )
            default_visible = style_key in {"BOITE", "SITE", "CABLE"}
            layers.append((layer, style_key, "04 设施与线路方案", default_visible))
        for layer, name, _section, _visible in layers:
            if not layer.isValid():
                raise ValueError(f"无法加载连接成果：{name}")
            if layer.geometryType() == 2 and any(not feature.geometry().isGeosValid() for feature in layer.getFeatures()):
                raise ValueError(f"候选分区几何无效：{name}")
            self._style(layer, name)
        project = QgsProject.instance()
        group = project.layerTreeRoot().addGroup("FTTH 本批次接线 " + Path(result["output_dir"]).name)
        sections = {name: group.addGroup(name) for name in (
            "01 正式输入", "02 服务区规划", "03 环境感知辅助", "04 设施与线路方案"
        )}
        for layer, style_name, section, visible in layers:
            project.addMapLayer(layer, False)
            node = sections[section].addLayer(layer)
            node.setItemVisibilityChecked(bool(visible))
        self._add_reference_basemap(project, group)

    def closeEvent(self, event):
        if self._bridge_process is not None or self._export_process is not None:
            self.status_label.setText("算法、成果导出或 AI 环境检查正在运行，请等待完成后关闭。")
            event.ignore()
        else:
            self.model_client.cancel_all()
            super().closeEvent(event)

    def reject(self):
        if self._bridge_process is None and self._export_process is None:
            self.model_client.cancel_all()
            super().reject()

    def _delivery_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("从一次成功的连续运行批次生成八个正式图层、统一GPKG、设备/线路/服务关系/统计表、道路节点、规划图、PDF图纸、校验报告和可追溯清单。任一关键校验失败时不生成不完整成果。")
        note.setWordWrap(True)
        layout.addWidget(note)
        export_form = QFormLayout()
        self.export_run_path = QLineEdit()
        run_browse = QPushButton("选择…")
        run_browse.clicked.connect(self._browse_export_run)
        run_row = QHBoxLayout()
        run_row.addWidget(self.export_run_path)
        run_row.addWidget(run_browse)
        export_form.addRow("连续运行批次", run_row)
        self.export_output_root = QLineEdit()
        self.export_output_root.setPlaceholderText("选择成果包输出根目录；每次生成唯一新目录")
        output_browse = QPushButton("选择…")
        output_browse.clicked.connect(self._browse_export_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.export_output_root)
        output_row.addWidget(output_browse)
        export_form.addRow("成果输出目录", output_row)
        layout.addLayout(export_form)
        options = QHBoxLayout()
        fixed = QLabel("固定输出：SHP八图层 + CSV清单")
        self.export_gpkg = QCheckBox("统一GPKG")
        self.export_gpkg.setChecked(True)
        self.export_png = QCheckBox("PNG规划图")
        self.export_png.setChecked(True)
        self.export_pdf = QCheckBox("PDF图纸")
        self.export_pdf.setChecked(True)
        options.addWidget(fixed)
        options.addWidget(self.export_gpkg)
        options.addWidget(self.export_png)
        options.addWidget(self.export_pdf)
        layout.addLayout(options)
        export_button = QPushButton("校验并生成完整成果包")
        export_button.setStyleSheet("font-weight: 600; padding: 7px 14px;")
        export_button.clicked.connect(self.start_export)
        layout.addWidget(export_button)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        legacy_note = QLabel("也可选择已有shape目录，单独执行八图层合同复核并加载到QGIS。")
        legacy_note.setWordWrap(True)
        layout.addWidget(legacy_note)
        form = QFormLayout()
        self.shape_path = QLineEdit()
        browse = QPushButton("选择…")
        browse.clicked.connect(self._browse_shape_dir)
        row = QHBoxLayout()
        row.addWidget(self.shape_path)
        row.addWidget(browse)
        form.addRow("最终 shape 目录", row)
        self.report_path = QLineEdit()
        report_browse = QPushButton("另存为…")
        report_browse.clicked.connect(self._browse_report)
        report_row = QHBoxLayout()
        report_row.addWidget(self.report_path)
        report_row.addWidget(report_browse)
        form.addRow("校验报告", report_row)
        layout.addLayout(form)
        button = QPushButton("校验并加载 8 图层")
        button.setStyleSheet("font-weight: 600; padding: 7px 14px;")
        button.clicked.connect(self.validate_delivery)
        line = QHBoxLayout()
        line.addWidget(button)
        line.addStretch(1)
        layout.addLayout(line)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _browse_export_run(self):
        path = QFileDialog.getExistingDirectory(self, "选择连续运行批次目录", self.export_run_path.text())
        if path:
            self.export_run_path.setText(path)

    def _browse_export_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择成果发布目录", self.export_output_root.text())
        if path:
            self.export_output_root.setText(path)

    def start_export(self) -> bool:
        """Build and publish a complete delivery package in a QGIS worker."""
        if self._bridge_process is not None or self._export_process is not None:
            return False
        self._set_running("正在核验连续批次并组装完整成果包…")
        self.tabs.setEnabled(False)
        try:
            run_dir = Path(self.export_run_path.text().strip()).expanduser().resolve(strict=True)
            if not run_dir.is_dir():
                raise ValueError("连续运行批次必须是目录。")
            output_text = self.export_output_root.text().strip()
            if not output_text:
                raise ValueError("请选择成果发布目录。")
            output_root = Path(output_text).expanduser().resolve()
            worker = Path(self.worker_python.text().strip()).expanduser().resolve(strict=True)
            if not worker.is_file() or not worker.name.lower().startswith("python"):
                raise ValueError("请选择 QGIS 对应的 python.exe。")
            arguments = [
                "-u", str(self.plugin_dir / "delivery_exporter.py"),
                "--run-dir", str(run_dir), "--output-root", str(output_root),
            ]
            if not self.export_gpkg.isChecked():
                arguments.append("--no-gpkg")
            if not self.export_png.isChecked():
                arguments.append("--no-png")
            if not self.export_pdf.isChecked():
                arguments.append("--no-pdf")

            process = QProcess(self)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            environment = QProcessEnvironment.systemEnvironment()
            prefix = QgsApplication.prefixPath()
            environment.insert("QGIS_PREFIX_PATH", prefix)
            environment.insert("PYTHONPATH", os.pathsep.join([str(Path(prefix) / "python"), environment.value("PYTHONPATH")]))
            environment.insert("PYTHONIOENCODING", "utf-8")
            environment.insert("PYTHONUTF8", "1")
            environment.insert("PYTHONDONTWRITEBYTECODE", "1")
            environment.insert("QT_QPA_PLATFORM", "offscreen")
            environment.insert("MPLBACKEND", "Agg")
            process.setProcessEnvironment(environment)
            process.setWorkingDirectory(str(run_dir))
            process.readyReadStandardOutput.connect(self._read_export_output)
            process.finished.connect(self._export_finished)
            process.errorOccurred.connect(self._export_error)
            self._export_output = bytearray()
            self.log.clear()
            self._export_process = process
            process.start(str(worker), arguments)
            return True
        except Exception as exc:
            self.tabs.setEnabled(True)
            result = {"status": "failed", "errors": [str(exc)]}
            self._show_json(result)
            self._finish(False, f"成果导出启动失败：{exc}", result)
            return False

    def _read_export_output(self):
        if self._export_process is None:
            return
        self._export_output.extend(bytes(self._export_process.readAllStandardOutput()))
        del self._export_output[:-2 * 1024 * 1024]
        self.log.setPlainText(self._export_output.decode("utf-8", errors="replace"))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _export_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._export_finished(-1, QProcess.ExitStatus.CrashExit)

    def _export_finished(self, exit_code, exit_status):
        process = self._export_process
        if process is None:
            return
        self._read_export_output()
        self._export_process = None
        self.tabs.setEnabled(True)
        result = {"status": "failed", "errors": [f"导出进程未返回结构化响应（退出码 {exit_code}）：{process.errorString()}"]}
        try:
            for line in reversed(self._export_output.decode("utf-8", errors="replace").splitlines()):
                if line.startswith(EXPORT_RESULT_PREFIX):
                    result = json.loads(line[len(EXPORT_RESULT_PREFIX):])
                    break
            if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
                result["status"] = "failed"
                result.setdefault("errors", []).append(f"成果导出工作进程异常退出：{exit_code}")
            if result.get("status") == "success":
                shape_dir = Path(result["shape_dir"])
                self.shape_path.setText(str(shape_dir))
                self.report_path.setText(str(result["validation_report"]))
                self._remember_report(result.get("validation_report"))
                try:
                    self._load_shape_layers(shape_dir)
                    self._zoom_to_project()
                except Exception as exc:
                    result.setdefault("warnings", []).append(f"成果已发布，但地图加载失败：{exc}")
                display = {
                    "status": result["status"], "version": result.get("version"),
                    "output_dir": result.get("output_dir"), "shape_dir": result.get("shape_dir"),
                    "geopackage": result.get("geopackage"), "tables_dir": result.get("tables_dir"),
                    "maps_dir": result.get("maps_dir"), "file_count": result.get("file_count"),
                    "validation": result.get("validation"), "warnings": result.get("warnings") or [],
                }
                self._show_json(display)
                self._finish(True, "完整成果包已通过校验并生成", result)
            else:
                staging = result.get("staging_dir")
                self._remember_report(str(Path(staging) / "failure_report.json") if staging else None)
                self._show_json(result)
                self._finish(False, "成果包校验或组装失败；未生成不完整成果", result)
        except Exception as exc:
            result = {"status": "failed", "errors": [f"成果导出响应处理失败：{exc}"]}
            self._show_json(result)
            self._finish(False, result["errors"][0], result)
        finally:
            process.deleteLater()

    def _browse_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择任务一配置", self.config_path.text(), "JSON (*.json)")
        if path:
            self.config_path.setText(path)

    def _browse_quick_source(self, name):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"选择 {name} 输入数据",
            self.quick_sources[name].text(),
            "支持的数据 (*.csv *.xls *.xlsx *.shp *.gpkg *.geojson *.json *.gml *.kml *.sqlite);;所有文件 (*)",
        )
        if not path:
            return
        self.quick_sources[name].setText(path)
        suffix = Path(path).suffix.lower()
        if suffix in {".xls", ".xlsx", ".gpkg", ".sqlite"} and not self.quick_sublayers[name].text().strip():
            self.quick_sublayers[name].setText(name)
        if not self.generated_config_path.text().strip():
            self.generated_config_path.setText(str(Path(path).parent / "ftth_task1_config.json"))

    def _browse_generated_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存自动生成的任务一配置", self.generated_config_path.text(), "JSON (*.json)"
        )
        if path:
            self.generated_config_path.setText(path if path.lower().endswith(".json") else path + ".json")

    def generate_input_config(self, run_after=False) -> bool:
        if self._bridge_process is not None or self._export_process is not None:
            return False
        try:
            selected = {
                name: {
                    "path": self.quick_sources[name].text().strip(),
                    "source_layer": self.quick_sublayers[name].text().strip(),
                }
                for name in BUSINESS_LAYERS
            }
            output = self.generated_config_path.text().strip()
            if not output:
                first = next((value["path"] for value in selected.values() if value["path"]), "")
                if not first:
                    raise ValueError("请至少先选择输入文件。")
                output = str(Path(first).expanduser().resolve().parent / "ftth_task1_config.json")
                self.generated_config_path.setText(output)
            result = build_task1_config(
                self.plugin_dir / "task1_config.json",
                output,
                selected,
                default_crs=self.input_crs.text().strip() or "EPSG:4326",
            )
            self.config_path.setText(result["config_path"])
            missing = set(result.get("missing_optional") or [])
            task3_ready = bool(result.get("task3_ready"))
            self.run_task3.setChecked(task3_ready)
            self.run_downstream.setChecked(task3_ready)
            has_formal_infrastructure = "INFRASTRUCTURE" not in missing
            self.use_formal_infrastructure.setChecked(has_formal_infrastructure)
            self.use_formal_infrastructure.setEnabled(False)
            if task3_ready:
                self.input_readiness_status.setText(
                    "输入具备任务二和任务三条件；运行任务六前仍须确认路径来源与容量。"
                )
                self.input_readiness_status.setStyleSheet("color: #2d6a4f; font-weight: 600;")
            else:
                names = "、".join(result.get("missing_optional") or [])
                self.input_readiness_status.setText(
                    f"已采用仅 IMB 模式。未提供：{names}。当前自动关闭任务三至六，"
                    "可先运行任务二分区；系统不会伪造现状设施。"
                )
                self.input_readiness_status.setStyleSheet("color: #9c6500; font-weight: 600;")
            self._show_json(result)
            self._finish(True, "输入配置已自动生成；原始数据未修改", result)
            if run_after:
                return self.build_base()
            return self.validate_base()
        except Exception as exc:
            result = {"status": "failed", "errors": str(exc).splitlines()}
            self._show_json(result)
            self._finish(False, f"自动生成配置失败：{exc}", result)
            return False

    def _browse_shape_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择八图层 shape 目录", self.shape_path.text())
        if path:
            self.shape_path.setText(path)
            if not self.report_path.text().strip():
                self.report_path.setText(str(Path(path) / "delivery_validation_report.json"))

    def _browse_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存校验报告", self.report_path.text(), "JSON (*.json)")
        if path:
            self.report_path.setText(path)

    def _engine_modules(self):
        engine_text = str(self.engine_dir)
        if engine_text not in sys.path:
            sys.path.insert(0, engine_text)
        from run_task1 import run
        from delivery_validator import run as validate_delivery

        return run, validate_delivery

    def _set_running(self, message: str):
        self.status_label.setText(message)
        self.status_label.setStyleSheet("font-weight: 600; color: #9c6500;")
        self.progress_bar.setValue(2)
        self.progress_bar.setFormat("2% · 正在准备")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def _finish(self, success: bool, message: str, result: dict[str, Any] | None = None):
        QApplication.restoreOverrideCursor()
        self.last_result = result
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"font-weight: 600; color: {'#2d6a4f' if success else '#b42318'};")
        self.progress_bar.setValue(100 if success else max(1, self.progress_bar.value()))
        self.progress_bar.setFormat("100% · 完成" if success else f"停在 {self.progress_bar.value()}% · 请查看错误")
        level = Qgis.MessageLevel.Success if success else Qgis.MessageLevel.Critical
        if hasattr(self.iface, "messageBar"):
            # QGIS 4 no longer accepts the QGIS 3-era integer values 0/2 for
            # the MessageLevel enum.  A notification failure must never turn
            # a successfully completed algorithm into a failed result.
            try:
                self.iface.messageBar().pushMessage(
                    "FTTH 数据契约", message, level=level, duration=7
                )
            except Exception:
                pass

    def _show_json(self, payload: dict[str, Any]):
        self.log.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def _remember_report(self, value: str | None):
        self._last_report = Path(value).resolve() if value else None
        self.open_report_button.setEnabled(bool(self._last_report and self._last_report.exists()))

    def _open_report(self):
        if getattr(self, "_last_report", None) and self._last_report.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_report)))

    def validate_base(self) -> bool:
        if self._bridge_process is not None or self._export_process is not None:
            return False
        self._set_running("正在校验基础数据…")
        try:
            run, _ = self._engine_modules()
            result = run(Path(self.config_path.text().strip()), validate_only=True)
            self._show_json({"status": result["status"], "summary": result["report"]["summary"]})
            self._remember_report(result.get("quality_report"))
            self._finish(True, "基础数据校验通过", result)
            return True
        except Exception as exc:
            self.log.setPlainText(traceback.format_exc())
            self._finish(False, f"基础数据校验失败：{exc}", {"status": "failed", "errors": [str(exc)]})
            return False

    def build_base(self) -> bool:
        if self._bridge_process is not None or self._export_process is not None:
            return False
        self._set_running("正在生成并加载基础图层…")
        try:
            run, _ = self._engine_modules()
            result = run(Path(self.config_path.text().strip()), validate_only=False)
            self._show_json({
                "status": result["status"],
                "version": result["version"],
                "feature_counts": result.get("feature_counts"),
                "geopackage": result.get("geopackage"),
                "task2_topology_json": result.get("task2_topology_json"),
                "warnings": result.get("runtime_warnings"),
            })
            self._remember_report(result.get("quality_report"))
            self._zoom_to_project()
            self._finish(True, "4 个基础图层已生成并加载", result)
            return True
        except Exception as exc:
            self.log.setPlainText(traceback.format_exc())
            self._finish(False, f"生成图层失败：{exc}", {"status": "failed", "errors": [str(exc)]})
            return False

    def validate_delivery(self) -> bool:
        if self._bridge_process is not None or self._export_process is not None:
            return False
        self._set_running("正在校验最终 8 图层…")
        try:
            _, validate_delivery = self._engine_modules()
            shape_dir = Path(self.shape_path.text().strip())
            report = Path(self.report_path.text().strip()) if self.report_path.text().strip() else shape_dir / "delivery_validation_report.json"
            result = validate_delivery(shape_dir, project_crs="EPSG:4326", output=report)
            self._show_json(result)
            self._remember_report(str(report))
            if result.get("status") != "passed":
                self._finish(False, f"8 图层校验未通过：{len(result.get('errors') or [])} 个问题", result)
                return False
            self._load_shape_layers(shape_dir)
            self._zoom_to_project()
            self._finish(True, "8 图层校验通过并已加载", result)
            return True
        except Exception as exc:
            self.log.setPlainText(traceback.format_exc())
            self._finish(False, f"8 图层校验失败：{exc}", {"status": "failed", "errors": [str(exc)]})
            return False

    def _load_shape_layers(self, shape_dir: Path):
        project = QgsProject.instance()
        group = project.layerTreeRoot().findGroup("FTTH 8图层验收") or project.layerTreeRoot().addGroup("FTTH 8图层验收")
        for name in ("SITE", "BOITE", "PTECH", "IMB", "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO"):
            exact = [path for path in shape_dir.glob("*.shp") if path.stem.upper() == name]
            matches = exact or [path for path in shape_dir.glob("*.shp") if path.stem.upper().endswith(name)]
            if len(matches) != 1:
                continue
            layer = QgsVectorLayer(str(matches[0]), name, "ogr")
            if not layer.isValid():
                raise ValueError(f"QGIS 无法加载 {matches[0].name}")
            self._style(layer, name)
            project.addMapLayer(layer, False)
            node = group.addLayer(layer)
            if name in {"ZNRO", "ZPM"}:
                node.setItemVisibilityChecked(False)
        self._add_reference_basemap(project, group)

    @staticmethod
    def _add_reference_basemap(project: QgsProject, group):
        """Append the same OpenStreetMap XYZ base layer used by the reference QGZ."""
        uri = (
            "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            "&zmax=19&zmin=0&crs=EPSG:3857"
        )
        layer = QgsRasterLayer(uri, "OpenStreetMap", "wms")
        if layer.isValid():
            project.addMapLayer(layer, False)
            group.addLayer(layer)

    @staticmethod
    def _style(layer: QgsVectorLayer, name: str):
        # These QML files are exported directly from the supplied
        # SHP JAD-MARJANE.qgz reference project.  Programmatic styling below
        # remains as a portable fallback if a QML file is missing or rejected.
        qml = Path(__file__).resolve().parent / "styles" / f"{name}.qml"
        cable_capacity_fields = ("CAPACITE", "cable_cores", "capacity", "required_capacity")
        cable_field = next((value for value in cable_capacity_fields if layer.fields().indexOf(value) >= 0), "")
        cable_has_nonstandard_capacity = False
        if name == "CABLE" and cable_field:
            field_index = layer.fields().indexOf(cable_field)
            cable_has_nonstandard_capacity = any(
                int(float(value or 0)) not in {24, 48, 72, 144}
                for value in layer.uniqueValues(field_index)
            )
        if qml.is_file() and not (
            name == "CABLE" and (cable_field != "CAPACITE" or cable_has_nonstandard_capacity)
        ):
            outcome = layer.loadNamedStyle(str(qml))
            ok = bool(outcome[1]) if isinstance(outcome, tuple) and len(outcome) > 1 else False
            if ok:
                layer.triggerRepaint()
                return
        spec = REFERENCE_LAYER_STYLES.get(name)
        if spec is None:
            color = AUXILIARY_LAYER_COLORS.get(name, "#6B7280")
            if layer.geometryType() == 0:
                symbol = QgsMarkerSymbol.createSimple({"color": color, "size": "3.2", "outline_color": "white", "outline_width": "0.4"})
            elif layer.geometryType() == 1:
                symbol = QgsLineSymbol.createSimple({"color": color, "width": "0.8"})
            else:
                fill = QColor(color)
                fill.setAlpha(45)
                symbol = QgsFillSymbol.createSimple({"color": fill.name(QColor.NameFormat.HexArgb), "outline_color": color, "outline_width": "0.7"})
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.triggerRepaint()
            return

        kind = spec["kind"]
        if kind == "point":
            symbol = QgsMarkerSymbol.createSimple({
                "name": str(spec["shape"]), "color": str(spec["color"]),
                "size": str(spec["size"]), "outline_color": str(spec["outline_color"]),
                "outline_width": str(spec["outline_width"]),
            })
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        elif kind == "line":
            symbol = QgsLineSymbol.createSimple({"color": str(spec["color"]), "width": str(spec["width"])})
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        elif kind == "polygon":
            symbol = QgsFillSymbol.createSimple({
                "color": str(spec["color"]), "outline_color": str(spec["outline_color"]),
                "outline_width": str(spec["outline_width"]),
            })
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        elif kind == "cable":
            colors = {24: "#CE53C6", 48: "#76CF10", 72: "#EE531F", 144: "#6C7BDF"}
            field_index = layer.fields().indexOf(cable_field) if cable_field else -1
            if field_index >= 0:
                field = layer.fields()[field_index]
                type_name = str(field.typeName()).lower()
                is_text = "char" in type_name or "text" in type_name or "string" in type_name
                categories = []
                values = sorted(layer.uniqueValues(field_index), key=lambda value: float(value or 0))
                for raw_value in values:
                    numeric = int(float(raw_value or 0))
                    capacity = min(colors, key=lambda item: abs(item - numeric))
                    color = colors[capacity]
                    value = str(raw_value) if is_text else raw_value
                    symbol = QgsLineSymbol.createSimple({"color": color, "width": "0.66"})
                    categories.append(QgsRendererCategory(value, symbol, str(raw_value)))
                layer.setRenderer(QgsCategorizedSymbolRenderer(cable_field, categories))
            else:
                symbol = QgsLineSymbol.createSimple({"color": "#6C7BDF", "width": "0.66"})
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        label_field = str(spec.get("label") or "")
        if label_field and layer.fields().indexOf(label_field) >= 0:
            settings = QgsPalLayerSettings()
            settings.enabled = True
            settings.fieldName = label_field
            text_format = QgsTextFormat()
            font = QFont("Open Sans")
            font.setBold(True)
            font.setItalic(True)
            text_format.setFont(font)
            text_format.setSize(10.0)
            text_format.setColor(QColor(str(spec["label_color"])))
            settings.setFormat(text_format)
            layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
            layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def _zoom_to_project(self):
        if not hasattr(self.iface, "mapCanvas"):
            return
        layers = list(QgsProject.instance().mapLayers().values())
        if not layers:
            return
        extent = None
        for layer in layers:
            if not layer.isValid() or layer.featureCount() == 0:
                continue
            extent = layer.extent() if extent is None else extent.combineExtentWith(layer.extent()) or extent
        if extent is not None:
            self.iface.mapCanvas().setExtent(extent)
            self.iface.mapCanvas().refresh()
