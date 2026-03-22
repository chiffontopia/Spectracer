# MIDI 网格与变速时间线设计

## 背景

下一阶段的 MIDI 编辑必须先把“时间网格”建立好，因为后续所有音符编辑、量化、拖拽、导出都依赖统一的拍点模型。用户当前最关心的不是复杂音符操作，而是：

- 可设置拍号（默认 4/4）
- 可设置 BPM
- 可设置整体偏移量（ms）
- 所有 MIDI 音符都基于网格定位
- 将来可扩展到分段变速与线性变速

## 设计目标

1. 提供一个**统一的音乐时间线模型**，支持：
   - 恒定 BPM
   - 分段恒定 BPM
   - 线性 BPM ramp
   - 拍号变化
   - 整体偏移
2. 保持 UI 主坐标仍然是 `seconds`，但是为每个小节增加数字标号
3. 网格与音符内部编辑坐标使用稳定的**音乐位置坐标**
4. 后续可直接挂接：
   - 网格线渲染
   - 音符量化
   - 事件轨道编辑
   - MIDI 导出

## 核心决策

### 1. 内部音乐坐标使用 quarter-note beat

内部统一使用“**四分音符拍**”作为音乐时间单位，而不是直接把当前拍号分母绑定到内部坐标。

原因：

- 与 MIDI tempo meta event 更一致
- BPM 在内部可稳定解释为 quarter notes per minute
- 拍号变化只影响**小节划分与主拍显示**，不直接破坏 tempo 数学模型
- 方便做线性 BPM 积分与反解

这意味着：

- 4/4 中一小节 = 4.0 quarter beats
- 3/4 中一小节 = 3.0 quarter beats
- 6/8 中一小节 = 3.0 quarter beats，但主网格可按八分音符继续显示

### 2. Tempo map 与 meter map 分离

时间线拆成两套事件：

- `TempoEvent`
  - `beat_position`
  - `bpm`
  - `transition`（step / linear）
- `TimeSignatureEvent`
  - `beat_position`
  - `numerator`
  - `denominator`

这样：

- 分段 BPM 与线性 BPM 由 tempo map 负责
- 拍号变化由 meter map 负责
- 两者可独立扩展

### 3. 线性 BPM 采用 segment interpolation

线性 BPM 的语义定义为：

- 某个 tempo event 到下一个 tempo event 之间
- BPM 从当前事件值连续线性变化到下一事件值

好处：

- 事件轨道可直接编辑
- 可自然表达 ramp
- 可在数学上做解析积分，不必靠数值近似

### 4. 偏移量作用于 seconds 域

偏移量以毫秒输入，内部转换为秒后加在 `beat <-> seconds` 映射最外层。

语义：

- `beat=0` 对应的实际音频时间不一定是 `0s`
- offset > 0：第一拍出现在音频更后面
- offset < 0：第一拍在音频更前面

## 数据结构

建议新增模块 `spectracer.midi.grid`，包含：

- `TempoTransition`
- `TempoEvent`
- `TimeSignature`
- `TimeSignatureEvent`
- `GridDivision`
- `GridLine`
- `MidiGridTimeline`

## 关键能力

### 1. 时间换算

- `beat_to_seconds(beat)`
- `seconds_to_beat(seconds)`

要求：

- step tempo 精确换算
- linear tempo 使用解析公式换算
- 支持 offset

### 2. 小节/主拍定位

- 给定 beat，求当前拍号
- 给定 beat，求所在小节、拍内位置
- 生成指定时长内的 bar / beat / subdivision 网格线

### 3. 量化

- `quantize_beat(...)`
- `quantize_seconds(...)`

音符编辑阶段将统一先量化到 beat，再映射回 seconds。

## UI 第一阶段策略

### 本阶段立即落地

1. 增加基础网格设置入口：
   - 拍号
   - BPM
   - 偏移 ms
   - 网格显示按钮
2. 在热图上绘制：
   - 小节线
   - 主拍线
   - 子网格线
3. 视图内部保存 `MidiGridTimeline`
4. 后续所有 MIDI note 编辑都基于该 timeline

### 暂缓到下一阶段

1. 顶部 tempo/meter 事件轨道的完整交互编辑
2. 线性 ramp 可视化曲线编辑器
3. 多轨 note 编辑操作
4. Undo/Redo

但本次数据模型必须**先支持**这些场景，避免返工。

## 渲染分层建议

`SpectrogramView` 后续建议分层：

- 热图层
- 网格层
- 游标层
- MIDI note overlay 层
- 交互辅助层

本次先把网格层接入即可。

## 风险与约束

### 风险 1：长音频 + 高频子网格导致绘制项过多

解决：

- 先用 path item 聚合绘制，避免每条线单独建 item
- 视口级裁剪可后续优化

### 风险 2：拍号分母变化导致“主拍”语义复杂

解决：

- 内部仍用 quarter beat
- 网格显示时根据 `denominator` 计算基础拍距
- 量化层按当前拍号上下文解释 division

### 风险 3：线性 BPM seconds_to_beat 反解出错

解决：

- 使用解析反函数
- 对极小斜率回退到常速公式
- 用单元测试覆盖 step / linear / offset / meter change

## 分阶段落地建议

### G1（当前）

- 时间线模型
- 恒定 BPM + offset UI
- 基础网格渲染
- 测试

### G2

- tempo event / meter event 列表模型
- 顶部事件轨道
- 分段变速与拍号变化 UI

### G3

- 线性 ramp 编辑
- note snap / quantize / drag integration
- MIDI 导出接入 tempo map

## 结论

先把 **时间线模型** 做对，再把 **网格渲染与基础参数设置** 接上，是进入实际 MIDI 编辑前最稳妥的路线。这样既能立即满足“拍号 / BPM / 偏移 + 网格定位”的核心需求，又不会把未来的分段变速和线性变速堵死。
