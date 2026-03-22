# MIDI 覆盖层与编辑模式设计

## 背景

当前 Spectracer 已经具备：

- 热图浏览与播放控制
- 拍号 / BPM / 偏移量网格
- Tempo / Meter 事件轨道
- MIDI 试听链路

下一步要进入真正的 **MIDI 音符编辑** 阶段。用户已经明确提出：

1. MIDI 覆盖层必须是独立层
2. 默认不可见，只有进入编辑模式才可见
3. 编辑模式下热图变暗，程度可调
4. 编辑模式下有明确的鼠标工具模式（放置 / 选择 / 擦除）
5. 最小支持 32 分音符
6. 播放时同时播放背景乐与 MIDI 音符，并可分别调节电平
7. 支持多通道编辑，并能为通道配置名称 / 乐器 / 声像 / 颜色
8. 必须具备撤销 / 重做
9. 导出 MIDI 时包含 Tempo / Meter 事件

此外还必须考虑已有的节拍时间线支持：

- 恒定 BPM
- 分段恒定 BPM
- 线性 BPM 变化
- 拍号变化
- 偏移量

因此 MIDI 覆盖层的设计，不能只把音符画在热图上，而必须围绕“**独立模型 + 独立层 + 网格驱动编辑**”来做。

---

## 设计目标

1. 建立一个**独立于热图渲染**的 MIDI 覆盖层系统
2. 所有音符编辑都基于现有 `MidiGridTimeline`
3. 覆盖层默认隐藏，仅在编辑模式进入时显示
4. 明确三种编辑工具：
   - 放置
   - 选择
   - 擦除
5. 音符内部坐标统一使用 **beat domain**，避免 tempo 改变时音符在音乐意义上漂移
6. 预留多通道、多颜色、多乐器、Pan/Velocity 编辑的扩展能力
7. 撤销 / 重做必须从一开始纳入架构，而不是后补
8. 导出 MIDI 时可直接复用 tempo / meter 时间线

---

## 核心设计决策

## 1. MIDI 覆盖层独立于热图层

覆盖层不能直接和 `SpectrogramView` 的热图绘制逻辑耦合。

建议分层：

- 热图图像层
- 网格层
- 游标层
- **MIDI 覆盖层**
- 交互辅助层（框选框、放置预览、拖拽预览、编辑暗化遮罩）

其中 MIDI 覆盖层使用独立模型驱动，不从热图矩阵反推出音符状态。

### 结果

- 即使未来换热图算法/色盘/渲染技术，MIDI 层也不用跟着重写
- 网格和音符编辑可以单独测试
- 导出和撤销也可以独立于热图存在

---

## 2. MIDI 音符使用 beat domain 存储，而不是 seconds

### 建议数据结构

```text
MidiNote
  id
  pitch                # 0~127
  start_beat           # quarter-note beat domain
  duration_beats
  velocity             # 0~127
  channel              # 0~15
  selected             # runtime/UI state, 不建议持久化到最终导出结构
```

### 原因

- 用户的编辑是“按拍点定位”的，而不是按秒定位的
- tempo / meter 变化后，音符仍应保持在音乐位置上
- 导出 MIDI 时，beat -> tick 很自然
- 回放与绘制时再通过 `MidiGridTimeline.beat_to_seconds()` 转换到秒坐标

### 直接收益

- 非恒定 BPM 下的 note 不会漂移
- 网格吸附实现简单直接
- 未来 tempo event 编辑不会破坏 note 的音乐位置

---

## 3. “显示网格细分”和“编辑吸附精度”要分离

当前已经有显示网格细分（例如每拍 4 等分），但**最小支持 32 分音符**意味着编辑时不能只依赖当前画出来的网格线。

### 决策

区分两个概念：

1. **Display Grid Division**
   - 决定画多少可见网格线
   - 偏向视觉可读性
2. **Edit Snap Resolution**
   - 决定放置 / 拖动 / 拉伸时的吸附精度
   - 最小必须支持 **1/32 note**

### 建议最小吸附分辨率集合

- 1/4
- 1/8
- 1/16
- **1/32**
- 三连音可后续补充

### 内部表达

统一转换成 quarter-note beat 的步长：

- 1/4 note = 1.0 beat
- 1/8 note = 0.5 beat
- 1/16 note = 0.25 beat
- **1/32 note = 0.125 beat**

### 结论

编辑吸附精度和显示网格不绑定。即使用户当前只显示较稀疏的网格线，仍然可以按 1/32 音符精度放置 note。

---

## 4. 编辑模式是一个独立状态机

### 顶层编辑状态

```text
MidiEditorState
  enabled: bool
  tool: place | select | erase
  active_channel: int
  snap_enabled: bool
  snap_resolution
  darken_amount: float
  box_select_enabled: bool
```

### 行为定义

#### 非编辑模式

- MIDI 覆盖层隐藏
- 热图保持正常亮度
- 不响应 MIDI note 编辑交互
- 播放时仍可回放已有 MIDI 音符（若已存在）

#### 编辑模式

- MIDI 覆盖层显示
- 热图背景被暗化（遮罩 alpha 可调）
- 编辑工具栏 / 面板显示
- 鼠标交互切换到工具驱动模式

---

## 5. 编辑模式下的鼠标工具模型

## 5.1 放置工具（Place）

### 左键行为

- 单击：在当前网格点放置一个最小长度 note
- 按住拖动：从起点到终点创建一个 note，长度吸附到网格
- 当前放置的 note 所属通道 = `active_channel`

### 备注

- 放置的 pitch 由鼠标 Y 位置映射到最近的 MIDI pitch / 频率 bin
- 起点和终点都在 beat domain 下量化
- 最小长度为当前吸附精度对应的最小单位，至少支持 1/32

## 5.2 选择工具（Select）

### 左键行为

- 点选音符：选中单个 note
- 拖动空白区域：框选 note
- 对已选音符按住拖动：整体移动（时间 + 音高）

### 后续可扩展

- Shift 多选
- Ctrl 增减选
- 组移动

## 5.3 擦除工具（Erase）

### 左键行为

- 点击音符：删除该音符
- 拖动擦除：路径碰到的音符都删掉

### 决策

擦除工具单独存在，不把删除行为塞到选择工具里，避免误操作。

---

## 6. 右键快捷行为

对已选 note 右键弹出上下文菜单，第一阶段至少包含：

- 上移半音
- 下移半音
- Velocity 详细调整
- Pan 详细调整
- 删除

### 注意

- Pan 建议先作为 note 级可选属性，若未显式设置则继承通道 Pan
- 后续如果实现 note 级 Pan 太复杂，可先只允许编辑 channel Pan，并在菜单中跳到通道设置

---

## 7. 通道系统与通道配置

为了满足“不同通道名称 / 乐器 / 声像 / 颜色可自定义”，建议引入独立的通道配置模型：

```text
MidiChannelConfig
  channel                # 0~15
  name                   # 如 Piano RH / Piano LH / Bass
  program                # GM Program
  bank                   # 可选，默认 GM
  pan                     # -1.0 ~ 1.0 或 0~127
  color                  # note 颜色
  muted                  # 可选扩展
  solo                   # 可选扩展
```

### 当前放置通道

编辑模式中用户可选择一个 `active_channel`：

- 新建音符默认写入该通道
- 试听时该音符也按该通道配置发声
- 覆盖层渲染时按通道颜色显示

### 10 号通道特殊处理

- 仍然沿用当前 MIDI 后端逻辑
- 10 号通道视为打击乐通道
- 频道显示应明确标注为 Percussion / Drum
- 对打击乐通道的 note，pitch 对应的是鼓音映射而非旋律音高语义

### 决策

- 先允许 0~15 共 16 个通道都可编辑配置
- note 内只存 channel index，具体乐器/颜色从通道配置表查询

---

## 8. 背景音乐与 MIDI 电平分离

### 用户要求

无论是否处于编辑模式，播放时都要同时播放：

- 背景音频
- MIDI 音符

并且两者要有**独立电平控制**。

### 决策

增加独立 MixerState：

```text
PlaybackMixState
  background_gain
  midi_gain
```

### 行为

- `background_gain` 控制现有 `QMediaPlayer/QAudioOutput`
- `midi_gain` 控制 MIDI 合成输出
  - 对 FluidSynth：优先通过 synth gain / channel volume 控制
  - 对系统 MIDI 输出：通过 CC7（Channel Volume）控制

### UI 建议

新增两条滑动条：

- BG
- MIDI

可放在底部 transport 区，避免和编辑工具混在一起。

---

## 9. 撤销 / 重做必须使用命令模式

### 必须纳入的操作

- 新增 note
- 删除 note
- 移动 note
- 拉伸 note
- 批量移动 / 删除
- 修改 note velocity / pan
- 修改通道配置
- 修改编辑状态（是否记录可选）

### 建议结构

```text
Command
  do()
  undo()

CommandStack
  undo_stack
  redo_stack
```

### 第一阶段命令集合

- `AddNoteCommand`
- `DeleteNotesCommand`
- `MoveNotesCommand`
- `ResizeNotesCommand`
- `UpdateNotePropertyCommand`
- `UpdateChannelConfigCommand`

### 原则

- 渲染层不直接改数据
- 所有模型变更都经过命令栈
- 批量框选拖动要打包成一个命令，而不是每帧一个命令

---

## 10. 导出 MIDI 时包含 Tempo / Meter 事件

### Step / Meter 事件

这部分可直接导出：

- Tempo meta events
- Time Signature meta events

### 线性 BPM 的现实问题

标准 MIDI **没有原生“线性 BPM 包络”事件**，只有离散 tempo meta events。

### 决策

导出时：

- `step` 段直接导出一个 tempo event
- `linear` 段按设定的时间/拍点精度离散采样为多个 tempo events

### 采样策略建议

提供一个 exporter 内部策略：

- 默认按每拍或每半拍采样
- 后续可暴露高级导出设置（精度 vs 文件大小）

这样：

- 工具内部仍保留“真正的线性 tempo”模型
- 导出时做合理逼近

---

## 11. 覆盖层渲染建议

## 11.1 Note 外观

每个 note 显示为矩形：

- X = `beat -> seconds -> view x`
- Y = `pitch -> note row`
- Width = `duration_beats -> seconds span`
- Fill = channel color
- Border = 选中态/hover 态高亮

### 建议可视状态

- normal
- hover
- selected
- playing（可后续扩展）

## 11.2 编辑模式背景暗化

编辑模式下，在热图和 note 层之间增加一个 dim overlay：

```text
MidiEditBackdrop
  enabled
  darken_amount   # 0.0 ~ 1.0
```

### 要求

- 仅在编辑模式显示
- 实时可调
- 不影响网格 / note / 游标可见性

## 11.3 默认可见性

- 编辑模式关闭：note overlay 隐藏
- 编辑模式打开：note overlay 显示 + 热图暗化 + 工具可用

---

## 12. UI 结构建议

建议新增以下模块：

- `ui/overlays/midi_note_overlay.py`
- `ui/dialogs/midi_note_properties_dialog.py`（可后续）
- `ui/dialogs/channel_config_dialog.py`
- `midi/session.py` / `midi/project_state.py`

主窗口建议增加：

- 编辑模式切换按钮
- 工具选择按钮（Place / Select / Erase）
- 当前通道选择器
- Snap 开关 + 分辨率
- 背景暗化滑条
- BG / MIDI 电平滑条
- Undo / Redo 按钮

---

## 13. 分阶段落地建议

## O1：覆盖层与最小显示

- `MidiNote` / `MidiChannelConfig`
- Overlay 渲染
- 编辑模式显隐
- 背景暗化

## O2：工具模式与基础交互

- Place / Select / Erase
- 点选 / 框选
- 拖动
- 删除

## O3：属性与通道配置

- Velocity / Pan 编辑
- 通道名称 / 乐器 / 声像 / 颜色
- 当前放置通道切换

## O4：撤销重做与导出

- CommandStack
- Tempo / Meter 导出
- linear BPM 离散化导出策略

---

## 14. 风险与应对

### 风险 1：非恒定 BPM 下 note 渲染漂移

应对：

- note 永远用 beat domain 存储
- 每次只在渲染时换算到 seconds

### 风险 2：框选 / 拖动 / 擦除状态混乱

应对：

- 工具状态机明确分离
- 交互辅助层负责显示框选框 / 放置预览 / 拖拽预览

### 风险 3：撤销栈后补会返工

应对：

- 第一版编辑操作就用命令封装
- View 不直接改模型

### 风险 4：线性 BPM 导出与内部模型不一致

应对：

- 明确“内部是真实线性，导出是离散逼近”
- 给导出器定义固定采样策略与测试样例

---

## 15. 结论

MIDI 覆盖层必须以“**独立层 + beat domain 数据模型 + 网格驱动编辑 + 命令式修改**”为核心。只有这样，后续的：

- 非恒定 BPM
- 通道配置
- Undo/Redo
- Tempo/Meter 导出
- 真正的采谱工作流

才能稳健扩展，而不会被热图实现细节反向牵制。
