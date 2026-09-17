# VVA 视频导演接入与视频制作协议

## 连接

下文命令在 Skill 根目录运行。客户端只使用 Python 标准库，不需要安装第三方依赖。WorkBuddy 在 macOS
上优先用 `sh scripts/vva.sh ...`，在 Windows 上优先用 `scripts\vva.cmd ...`；两个启动器都会从自身所在
目录定位 `vva_client.py`，不依赖任务工作目录。其他 Agent 也可以继续直接运行下列命令。

```bash
python3 scripts/vva_client.py login-web
python3 scripts/vva_client.py whoami
python3 scripts/vva_client.py capabilities --output capabilities.json
python3 scripts/vva_client.py schema --output authoring-schema.json
python3 scripts/vva_client.py projects
```

发布包默认连接 `https://video.pictech.top/api/v1`，网页登录地址为 `https://video.pictech.top`。本地开发或私有部署才需要覆盖：

```bash
export VVA_API_URL='http://127.0.0.1:18000/api/v1'
export VVA_WEB_URL='http://127.0.0.1:15173'
```

`login-web` 创建一次性授权请求，尝试打开 VVA 网页，同时在终端打印授权链接。用户在网页完成现有账号登录并点击确认后，客户端自动取得会话；授权码十分钟内有效且只能使用一次。客户端只将会话令牌保存到 `~/.config/vva/session.json`，在支持 POSIX 文件权限的平台设置为 0600；Windows 使用当前用户配置目录并兼容其文件权限机制。客户端不输出令牌。不要让 Agent 读取此文件内容。也可由用户在执行环境设置 `VVA_ACCESS_TOKEN`；不是模型厂商的 API Key。

任何读取或同步前先执行 `whoami`。未登录或会话过期时，客户端以非零状态退出，并打印适用于当前 API、前端地址和凭证文件的 `login-web` 命令。Agent 运行该命令后，把打印的网页链接展示给用户，等待网页确认完成，再用 `whoami` 复核。不得在聊天中索要密码或令牌。403 表示当前已登录账号权限不足，不应误导用户反复登录。

`login --phone ...` 只作为真实交互式终端中的备用方式。Claude Code、Codex、WorkBuddy 等非 TTY 环境会明确拒绝读取密码并提示改用 `login-web`，不会再输出 `getpass` 堆栈。VVA 只使用手机号账号，不提供邮箱注册或登录。

网页登录使用 Atlas 手机号账号；浏览器确认后，Skill 获得一枚独立、可撤销的 VVA 会话凭证，默认有效期 30 天。该账号能访问哪些作品，Skill 就能访问哪些作品。远程服务必须使用 HTTPS；更换 API 地址需要重新登录，防止把旧令牌发给其他站点。

## 新建与接续

VVA 的新建输入应来自当前上下文中已有的故事构想、大纲或剧本。若用户只表达“想做视频”而没有故事内容，先请其提供或在当前 Agent 中完成大纲；不要先创建只有标题或通用占位内容的 VVA 作品。若当前对话已经包含用户认可的故事，不要求重复粘贴。进入下述新建流程后，VVA 的职责是把故事转成可生成的视频结构，而不是另行发散一个无关故事。

### 先匹配作品，禁止默认追加

准备任何 `create` 或 `push` 之前，先从用户请求或拟定剧本中确定本次故事名称，并运行 `projects` 获取当前账号的作品列表。按以下顺序判定：

1. 用户明确要求新故事、新作品或另建作品：生成新的作品 UUID，使用 `create`。不要复用对话中上一部作品的 UUID。
2. 没有名称完全相同的已有作品：使用本次故事名称和新的作品 UUID 执行 `create`。不得把它作为新分集、片段或镜头追加到最近操作的作品。
3. 只有一个同名作品，且用户明确说继续、修改或补充它，或者提供了与其一致的链接/UUID：先 `pull` 该 UUID，再以最小变更执行 `push`。
4. 有多个同名作品、名称和用户给出的 UUID 不一致，或上下文无法判断用户要新建还是接续：不要调用写接口；列出足以区分的作品名称和更新时间，请用户确认目标。

“故事名称相同”是允许接续判断的必要条件，但不是自动修改的充分条件。角色重名、题材相似、当前浏览器打开某作品、最近一次 `pull` 的 UUID，都不能替代作品归属确认。用户明确指定已有作品链接/UUID时，以该目标为准并核对其名称；若名称不同，先询问用户是要改名后的同一作品，还是创建新作品。

同步完成后校验返回的 `project_id`：新建必须是本次新生成的 UUID；接续必须是用户确认的已有 UUID。若不一致，停止后续写入并报告，不要尝试把内容迁移或再次追加。

先获得用户的比例选择，再运行（这里只是格式示例，不是替用户选择 16:9）：

```bash
python3 scripts/vva_client.py new-plan --title '校门口的小插曲' --aspect-ratio '16:9' --output plan.json
python3 scripts/vva_client.py ids PROJECT_UUID character:zhanghai character:wenli scene:school-gate image:zhanghai:base image:wenli:base image:school-gate:base episode:1 segment:1 shot:1
```

`ids` 根据项目 UUID 和逻辑键计算 UUID5；后续保留这些 ID。重命名实体不要重新计算 ID，也不要把用户可改的名字当数据库主键。逻辑键只在规划时使用，服务端身份是 UUID。

新建请求结构：

```json
{
  "request_id": "一次写入的UUID",
  "project": {"id": "作品UUID", "title": "作品名", "aspect_ratio": "用户确认的比例"},
  "assets": [],
  "looks": [],
  "drafts": [],
  "episodes": [],
  "segments": [],
  "shots": []
}
```

这是字段说明，示意字符串需要替换为真实 UUID。完整字段、枚举、长度、必填规则以 `schema` 输出为准：

| 数组 | 创建时的主要字段 |
| --- | --- |
| assets | id、asset_type（character/scene/prop）、name |
| looks | id、character_asset_id、name |
| drafts | id、asset_id、kind、name、prompt、settings；装扮另填 character_look_id |
| episodes | id、title；可存本集 script_text |
| segments | id、episode_id、title |
| shots | id、segment_id、script_document、target_duration_ms、generation_mode、aspect_ratio、resolution、video_model；不要传 shot_no，由 VVA 自动编号 |

新作品比例必填，镜头通常沿用作品比例。镜头的 `resolution` 默认填写 `720P`；只有用户明确选择其他清晰度且服务器 `capabilities` 显示对应模型支持时才更改。同一镜头中有台词的角色最多 3 个，画外角色也计入；超过时拆分镜头。纯旁白或系统声音不算角色对白，但必须明确标注。界面时长是秒；协议 `target_duration_ms` 是毫秒，例如 5 秒写 5000。

镜头编号由服务端根据所在分集、片段和镜头位置统一生成，格式为 `E01-P01-S001`：分集和片段各补齐两位，镜头补齐三位。Skill 不得发送 `01`、`02`、`镜头1` 等自定义 `shot_no`；即使旧版计划带了该字段，新建时服务端也会改为工作台的统一编号。

```bash
python3 scripts/vva_client.py validate --create --file plan.json
python3 scripts/vva_client.py create --file plan.json --output created.json
python3 scripts/vva_client.py pull PROJECT_UUID --output latest.json
```

本地 `validate` 只做基础防错检查；服务端严格校验全部字段、关系和权限，并在一个事务中写入。失败不会留下半套作品。此专用新建接口不会额外产生默认的空分集、片段、镜头。

接续修改仍使用上述平铺数组，但只放本次修改的实体，带它们最新的 `expected_revision`。例如修改镜头脚本时保留其 id 和 segment_id，带 `expected_revision` 和新 script_document；不要发送其他镜头。修改作品自身时 `project` 包含 id、expected_revision 和需要修改的字段。

```bash
python3 scripts/vva_client.py push PROJECT_UUID --file changes.json --output updated.json
```

每个新的修改批次使用新的 request_id；同一批次失败/超时重试必须原样保留 request_id 和请求体。成功返回 `project_id`、`request_id`、`replayed`、`snapshot`。相同请求重放返回原结果；修改了内容却重用请求号会报冲突。`pull` 返回当前平铺快照，包含 revision 和生成状态；快照不是可直接回传的更新请求，不要把只读字段一并发送。

409 表示版本或请求冲突。先读取再对比，不要自动覆盖用户编辑。已有实体的父级、资产类型、草稿种类与装扮归属、position 顺序不能通过此版同步改变；普通更名、提示词和脚本修改保留它们原值即可。此接口不以缺席代表删除，也不允许伪造生成任务状态。需要删除时由用户在 VVA 的确认操作中进行。

## 本地图片上传与草稿替换

先读取服务器媒体能力和目标作品的最新快照。文件必须是用户明确指定的绝对路径、普通文件且不能是符号链接；当前支持服务端实际可解码的 PNG、JPEG、WebP。客户端会先按 `capabilities.media` 检查大小和类型，服务端会再次按真实文件内容解码验证，不信任扩展名或上传声明。

只上传、不绑定资产或草稿：

```bash
python3 scripts/vva_client.py media-upload PROJECT_UUID \
  --file /absolute/path/to/image.png \
  --request-id REQUEST_UUID \
  --output uploaded-media.json
```

成功返回 `ok`、`operation=media_upload`、`project_id`、`request_id`、`client_sha256`、完整 `media`、`binding=null` 和 `replayed`。该命令只创建媒体记录，不创建资产引用，不修改草稿、镜头或声音。

原子上传并替换图片草稿：

```bash
python3 scripts/vva_client.py media-replace PROJECT_UUID \
  --draft-id DRAFT_UUID \
  --asset-id ASSET_UUID \
  --expected-revision REVISION \
  --file /absolute/path/to/image.png \
  --request-id REQUEST_UUID \
  --output replaced-media.json
```

复用已经由 `media-upload` 写入同一作品的媒体时，改用：

```bash
python3 scripts/vva_client.py media-replace PROJECT_UUID \
  --draft-id DRAFT_UUID \
  --asset-id ASSET_UUID \
  --expected-revision REVISION \
  --media-file-id MEDIA_UUID \
  --request-id REQUEST_UUID \
  --output replaced-media.json
```

`--file` 与 `--media-file-id` 必须且只能提供一个。草稿只能是 `base`、`supplement` 或 `look`；服务端从草稿推导引用类型并校验作品、资产、装扮关系、图片实际内容和 revision。一次成功事务会创建或复用媒体、把该槽位旧引用转为非当前、创建带 `generation_draft_id` 的新当前引用、更新草稿媒体并清除未读标记。多个 supplement 草稿各自保留引用历史，不会互相取消。

成功结果同时给出 `previous_media`、`new_media`/`media`、完整 `draft`、`reference`、`binding` 和 `effects`。必须检查：

- `binding.current_media_file_id` 与 `draft.resolved_media_file_id` 等于新媒体 ID；
- `binding.draft_revision` 是新的 revision；后续修改使用它；
- `effects.slot_references_use_new_media=true`；
- `effects.fixed_media_references_changed=false`、`historical_generation_snapshots_changed=false`、`generation_started=false`。

同一账号、同一 request_id、同一目标与同一文件 SHA/媒体 ID 的重试返回第一次结果并设 `replayed=true`，即使成功后草稿 revision 已前进也不会重复创建。相同 request_id 搭配不同文件、作品或参数会返回 409。新的逻辑操作必须换新 request_id。

发生 409 时重新 `pull`，比较草稿最新 revision 和用户修改，不能自动覆盖。413 先压缩文件；415 换成真实可解码的支持格式；503 或连接超时表示存储结果可能不确定，先 `pull`，再用完全相同的 request_id 和参数重试。原子替换不会返回“文件已上传但草稿未绑定”的普通半成功结果：数据库失败时会清理新对象；若清理状态也不确定，错误会明确要求先读取核对。

替换只改变稳定草稿槽位的当前图片。未来使用 `media_slot_ids` 的图片/视频生成会解析新媒体；镜头脚本写死的 `media_file_ids`、已提交任务的输入快照、历史成片、旧媒体文件均不改写或删除。上传与替换绝不触发付费生成任务。

## 草稿就是可提前引用的图片位置

每个 draft 有稳定 id。kind 可选 `base`、`supplement`、`look`、`voice`：

- 基础图：为角色/场景/道具写具体可生成的视觉描述。首次搭建时，每个角色只创建一张 `base` 图片草稿，人物默认 `9:16` 正面全身、白底或浅灰底；场景和道具只创建必需的基础图。
- 声音：为角色保留 `kind=voice` 草稿，prompt 描述声线；settings.preview_text 填实际试读内容，可含 “Hello, nice to meet you.”。试读内容最多 100 个字符，标点、空格、英文均计入；建议自然生成约 5～15 秒，不强制时长。
- 装扮和补充图默认不创建，`looks` 保持空数组，`drafts` 中不放 `look` 或 `supplement`。只有用户明确要求时才增加；已有作品中的这些内容及引用继续保留，不做删除。
- 用户明确要求装扮时：建立 look，再建 kind=look 的 draft，关联 character_look_id，提示词描述服装变化和需保持的身份特征。
- 用户明确要求补充图时：每张图片对应一个 supplement draft。未指定视角时才采用 `16:9` 同图正、侧、背三视图；指定“左侧面特写”等构图时遵从用户要求。

图片 settings：model、aspect_ratio、resolution。声音 settings：model、preview_text、language。模型和可选设置从 capabilities 获取。

## 图片生成提示词质量规范

图片草稿不是给用户看的简短备注，而是会直接交给图片模型的生成指令。创建或修改 `base`、`supplement`、`look` 草稿时都执行本节；正向提示词默认使用中文，不为了迎合模型擅自翻译成英文。

### 通用要求

- 不得使用只有资产名称、风格词堆砌或一句笼统改图要求的低信息提示词。提示词要具体、连贯，说明主体、画面内容、构图、姿态或动作、环境、光线、镜头/对焦、材质与细节，以及必须避免的结果。
- 人物是主体时，必须说明脸部、眼睛、发丝、皮肤和服装纹理清晰，精准对焦，无运动模糊；全身图还要明确从头顶到鞋底完整入镜，并保留脚底与地面的自然接触关系。除非用户明确要浅景深，不要让脸部或身体落在焦外。
- 把角色设定中的年龄段、性别表达、脸型、五官、发型、体型、服装和关键辨识物写进提示词。不同角色必须有各自的有效特征，不能只替换姓名、复用同一份空泛描述。
- 提示词中的要求不能互相冲突。例如“白底角色设定图”和“拥挤夜市电影场景”、“正面固定机位”和“多角度拼图”不能同时出现。用户要求优先；发现冲突时先整理为一个明确目标。
- 不向 `settings` 私自增加 schema 没有声明的参数。VVA 会为支持的模型补充负面提示词，但正向提示词仍要明确本次画面的关键限制，不能依赖通用负面词猜测用户意图。
- 写入前检查提示词中没有 `[角色描述]`、`某某`、`待补充` 等占位符，没有与画面无关的解释，也没有要求模型在图片里生成供人阅读的说明文字。

### 人物基础形象 T2I

默认目标是可供后续角色一致性引用的清晰身份基准图，而不是剧情剧照。按下列结构组织并用真实角色设定替换方括号内容：

```text
专业商业目录风格的人物角色正面全身照。单人单视图，[年龄段、性别表达、身份和气质]，[脸型、五官、肤色、发型、体型、服装、鞋履和关键辨识物]。人物正对镜头自然站立，双臂自然下垂，从头顶到鞋底完整入镜，身体比例自然，鞋底与地面接触清楚。纯白或带柔和立体明暗的浅灰无缝摄影棚背景，背景干净。均匀柔和的棚拍光线，正面和轮廓受光清楚，曝光正常，白平衡中性，精准对焦，高细节，自然皮肤质感，眼睛、睫毛、发丝、服装结构和面料纹理清晰。不要裁切头顶、手脚或鞋底，不要浅景深和运动模糊，不要额外人物、重复肢体、拼图、文字、标志或水印。
```

这不是可原样提交的固定文案。方括号内必须根据故事补全；如果用户明确选择其他背景、姿态、风格或比例，则保留“身份清楚、主体完整、精准对焦、高细节”等质量要求并遵从用户选择。

### 单参考图 I2I、装扮与补充图

开头先说明如何使用参考图，再分别写“改变什么”和“保持什么”，不能只写“换衣服”“生成侧面”或“变年轻”。单参考图可采用以下结构：

```text
编辑第一张参考图片中的同一人物：[具体变化，例如把服装更换为浅蓝色衬衫、深灰长裤和黑色皮鞋]。[目标构图、姿态、背景和光线]。严格保持此人的脸型、五官、肤色、发型、年龄感、体型和其他身份辨识特征一致；保持人物清晰、比例自然、精准对焦，眼睛、发丝和服装材质细节清楚。不要改变身份，不要模糊或裁切主体，不要增加其他人物、文字、标志或水印。
```

- 装扮图重点描述新的服装、配饰、妆发变化，同时明确保留哪些身份特征。若妆发也要改变，要保留脸型、五官、年龄感和体型等仍可识别的锚点。
- 补充图重点描述同一基础形象的新视角、景别或局部。标准三视图要说明同一张 `16:9` 图片中依次为正面全身、严格 90 度侧面全身、背面全身，三人像等高、等比例、服装和光线一致且全部完整入镜。
- 多参考图要明确每张图的用途和优先级，例如“以第一张图的人物身份和脸部为准，以第二张图的服装为准”；不得只附多张图而不说明关系。

### 场景与道具

- 场景提示词写清地点、时代/时间、空间布局、主要区域、材质、光线、天气或氛围、镜头高度和景别；默认生成能看清空间关系的建立镜头。除非故事需要，不要让随机人物遮挡场景，也不要生成可读文字。
- 道具提示词写清类别、形状、尺寸感、材质、颜色、结构、磨损/新旧程度、关键部件、观察角度、背景和产品级布光；需要后续一致性时让主体居中、轮廓完整、细节清楚，避免手部或杂物遮挡。

### 模型档位与提交前检查

每次先读 `capabilities`。若可用模型中同时存在以下两档：

- `Vokwa Image（高质量）`：用于人物基础形象、关键场景/道具定稿，以及身份和细节优先的结果。
- `Vokwa Image Fast（快速）`：用于草图、构图试验、快速迭代，或用户明确优先速度的结果。

无论选择哪一档，生成草稿写入前确认：提示词已具体描述主体；人物脸部和全身完整性要求明确；光线、对焦和细节要求明确；参考图的用途明确；比例符合资产用途；模型和分辨率在 `capabilities` 内。任一项缺失时先补全提示词，不把低质量草稿同步给 VVA。

图片草稿可指定两种引用：

```json
{
  "reference_slot_ids": ["同一资产的基础图片draft UUID"],
  "reference_media_file_ids": []
}
```

`reference_slot_ids` 引用尚未生成或会被替换的当前图片位置；`reference_media_file_ids` 精确引用已有选定文件。选择空数组就是不使用参考图。不能把尚未生成的 slot 从列表删掉来绕开依赖等待；VVA 会提醒先生成依赖图片。

避免自引用和环路；基础图可无参考，装扮/补充图引用基础图，形成简单的先后顺序。不把不同角色的图片混为同一角色引用。

## 镜头结构化引用

新作品默认引用基础图片草稿，角色的 `character_look_id` 留空或省略。以下示例只依赖张海的基础形象，基础图尚未生成时也可先保存引用：

```json
{
  "type": "doc",
  "content": [
    {"type": "text", "text": "午后，校门口。中景缓慢推进，"},
    {
      "type": "asset_mention",
      "asset_id": "张海的资产UUID",
      "role": "character",
      "label": "张海",
      "media_slot_ids": ["张海基础形象draft UUID"],
      "media_file_ids": []
    },
    {"type": "text", "text": "抬手指向校门，轻声说：到了。"}
  ]
}
```

普通 `@名称` 文本不是有效的媒体绑定。不要猜已有媒体 UUID，先从快照读取。只有用户明确要求具体装扮/视角，或接续作品中已有对应引用时，才使用相应的 `character_look_id` 和图片草稿 ID；不要为丰富默认镜头引用而新增这些草稿。同一人物可以选择一张或多张参考，但不要无差别上传全部补充图。

图片生成成功后 slot 绑定当前文件，镜头生成时解析为实际参考图并冻结输入快照；历史任务不会随之后改图而改变。未生成的 slot 可保存进脚本，但不能悄悄当成无参考去生成视频。

首帧、首尾帧、普通分镜图的实际上传/指定仍使用 VVA 对应界面，不将它们伪装成 @资产。此 Skill 首版优先把多参考/纯文本镜头准备到可生成状态；选择其他方式时，应明确告诉用户还需在镜头输入区域指定帧图片。声音样片也不等于视频已绑定音色。
