# Spectracer

Spectracer 是一款面向音乐采谱（扒谱）场景的现代音频频谱分析与编辑工具

## 当前状态

目前已经打通并稳定可用的能力包括：

- 音频导入、CQT 分析、缓存写入与预览图输出
- CLI / GUI 双入口
- 多声道模式分析与切换（Stereo / Mono / L / R / L-R）
- 分析选项对话框中勾选要解析的声道模式
- 渐进式异步分析：优先加载默认显示声道，其余模式后台继续解析
- GUI 热图浏览：缩放、滚动、悬停查看音高/频率、琴键联动、高亮倍音
- 播放控制：播放/暂停、拖动定位、播放速率、游标同步
- 跟随游标滚动开关、快速定位游标位置
- 改进后的热图归一化策略：
  - 最大值 dB 归一化
  - 百分位 dB 归一化（当前更推荐）
- 自定义热图色盘：
  - 编辑颜色节点
  - 导入 / 导出 CSV
  - 导入时指定预设名称（默认采用文件名）
  - 自定义预设持久化保存到 `colormap_presets/`
- MIDI 试听链路（第一步）：
  - 点击钢琴键 / 热图可试听音高
  - 优先使用 `pyfluidsynth` + SoundFont（`.sf2`）
  - 若未找到可用 SF2，会自动回退到系统 GS 波表（Windows）
- MIDI 网格时间线（第一阶段）：
  - 支持常量 BPM、拍号与毫秒偏移量设置
  - 底层时间线模型已支持恒定 / 分段 / 线性 BPM 映射与量化接口
  - 频谱视图可显示基础小节 / 拍 / 子拍网格与小节编号
  - 频谱上方的 Tempo / Meter 事件轨道已支持折叠/展开（默认折叠），并可通过双击 / 右键 / Delete / 拖动进行基础交互编辑，且带事件吸附与重置入口

---

## 安装

### 使用 pip

如需 GUI、MIDI 与开发依赖，推荐：

```bash
pip install -e ".[ui,midi,dev]"
```

如果只需要基础 CLI：

```bash
pip install -e .
```

### 使用 uv

```bash
uv sync --extra ui --extra midi --extra dev
```

---

## 启动方式

### 启动 GUI

```bash
spectracer gui
```

或启动时直接打开一个音频文件：

```bash
spectracer gui ./demo.wav
```

也可以：

```bash
python -m spectracer gui
```

### 运行 CLI 分析

```bash
spectracer analyze ./demo.wav --output ./.spectracer_cache
```

使用配置文件（推荐）：

```bash
spectracer analyze ./demo.wav --config ./config/analysis.default.toml
```

可选参数示例：

```bash
spectracer analyze ./demo.wav \
  --channel-mode l-r \
  --fps 60 \
  --bins-per-semitone 2 \
  --octave-min 1 \
  --octave-max 8 \
  --a4 440 \
  --sample-rate 22050
```

### 初始化项目目录

```bash
spectracer init-project ./my_song
```

---

## 热图色盘预设（CSV）

色盘编辑器支持将当前颜色节点导出为 CSV，也支持从 CSV 导入为命名预设。

### CSV 格式

```csv
preset_name,MyPreset
pos,color
0.000000,#000000
0.250000,#0033ff
0.600000,#ffee00
1.000000,#ff0000
```

说明：

- `preset_name` 行可用于记录预设名称
- `pos` 取值范围建议为 `0 ~ 1`
- `color` 支持 `#RRGGBB`、`RRGGBB`、`0xRRGGBB`
- 导入后会自动做合法化处理并补齐端点
- 自定义预设会保存到仓库根目录下的 `colormap_presets/`

---

## 基准测试

使用测试音频批量跑分析耗时 / 峰值内存：

```bash
spectracer-benchmark --patterns "tests/*.wav"
```

会在 `.benchmarks/` 目录下输出 JSON / CSV 报告。

---

## 自动化测试

```bash
pytest
```

当前仓库已有针对配置、CLI、分析流程、模型层的自动化测试。

---

## MIDI 试听说明

当前 MIDI 试听在自动模式下会优先尝试 **系统 MIDI 输出**；若系统端口不可用，再回退到 `pyfluidsynth` + SoundFont（`.sf2`）。

- 会扫描 `soundfonts/` 目录下的任意 `.sf2` 文件（包含符号链接）
- 也可以通过环境变量 `SPECTRACER_SOUNDFONT` 指定单个 `.sf2` 文件或目录
- 工具栏中的 `MIDI...` 可设置输出端口、指定 SF2、乐器（Program）与通道
- 若显式指定输出端口，则输出端口优先；若端口保持“自动”且指定了 SF2，则优先使用该 SF2
- 10 号通道会按 GM 打击乐通道处理，并自动切换到 Drum Bank 128
- 工具栏中的 `网格...` 可设置 BPM、拍号、偏移量与每拍细分，`显示网格` 可快速开关覆盖层
- `网格...` 负责编辑起始 BPM / 拍号；更细的 Tempo / Meter 变化可在事件轨道中交互编辑
- 事件轨道默认折叠，可通过工具栏 `事件轨道` 按钮展开；展开后会随当前时间线、视口与播放游标同步刷新
- 事件轨道上方提供独立的编辑控制区：`事件吸附` 可切换新增 / 拖动事件时是否吸附到当前网格细分，`重置事件…` 会在二次确认后移除所有非起始 Tempo / Meter 事件
- 事件轨道支持：单击选中事件、双击空白处新增、双击事件编辑、右键快捷菜单、水平拖动非起始事件，以及 `Delete / Backspace` 删除非起始事件
- 状态栏会常驻显示当前 MIDI 通道、乐器 / 鼓组、优先模式与实际输出后端
- 热图试听改为鼠标按下即发声

---

## 目录结构（当前）

```text
config/
colormap_presets/
src/spectracer/
  app/
  audio/
  core/
  dsp/
  midi/
  project/
  tools/
  ui/
tests/
plans/
```

