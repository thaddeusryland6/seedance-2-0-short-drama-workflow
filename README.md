# Seedance 2.0 中文短剧：先草稿后成片

这是一个可审阅、可恢复的中文短剧命令行流程。它把每一集拆成稳定的 scene ID，先用 Seedance 2.0 Mini 或 Fast 做低成本草稿，人工确认镜头后再用标准 Seedance 2.0 渲染成片。每个阶段都有独立 manifest、request_id 和下载文件，最后用 ffmpeg 按场景顺序合成。它不是在线工作台，也不会在后台悄悄提交请求。

[查看 Seedance 2.0 模型页](https://argolink.io/en/models/seedance-2.0)，提交前重新核对限制和价格。Last checked: 2026-10-07。

## 为什么要分两个阶段

短剧最容易浪费额度的地方是错误的开场、动作读不清和人物连续性崩掉。Mini 适合先问“这个镜头能不能用”，Fast 适合审核等待时间成为瓶颈的情况，标准模型才用于已经批准的镜头。仓库不会把草稿自动升级成成片；审核者必须明确写下每场戏的批准备注。

当前目录中复核到的有效价（每个输出秒）：

| 模型 | 可用分辨率 | 480p | 720p | 1080p | 4K |
| --- | --- | ---: | ---: | ---: | ---: |
| seedance-2.0-mini | 480p、720p | $0.026 | $0.057 | — | — |
| seedance-2.0-fast | 480p、720p | $0.042 | $0.091 | — | — |
| seedance-2.0 | 480p、720p、1080p、4K | $0.053 | $0.11 | $0.28 | $0.58 |

这些是 2026-10-07 目录里的 ArgoLink 有效价格，不是账单保证。官方页面目前显示标准 Seedance 2.0 为官方价的 25% 折扣；参考视频秒数会按输出单价另计，图片和音频不增加视频秒数。失败、拒绝、过期任务是否收费以当日模型页和账户合同为准。

## 安装

需要 Python 3.9+ 和 ffmpeg。HTTP 客户端只用标准库，不需安装第三方 SDK。

~~~sh
git clone https://github.com/YOUR_ACCOUNT/seedance-2-0-short-drama-workflow
cd seedance-2-0-short-drama-workflow
python3 -m venv .venv
. .venv/bin/activate
ffmpeg -version
export VIDEO_API_KEY='从密钥管理器注入'
~~~

默认接口是 https://argolink.io/v1。兼容的本地测试服务可以设置 VIDEO_API_BASE_URL，但生产请求必须使用 HTTPS。真实 key 不放进计划文件、manifest、日志或 Git。

## 1. 创建分镜计划

每个位置参数是一场戏，程序先写本地 JSON，不发请求：

~~~sh
python -m drama.pipeline \
  --episode 1 --title "旧钥匙" \
  --references https://cdn.example.test/xiaoyu.png \
  --out episode-01-plan.json \
  "小雨在车站捡起一枚旧钥匙" \
  "她发现钥匙上的编号与失踪父亲的照片相同" \
  "雨停后她走向城南的旧影院"
~~~

计划包含草稿模型、成片模型、比例、分辨率、参考图和 scene-01 等稳定 ID。默认草稿是 Mini、成片是标准模型，二者均为 720p。参考图必须是 HTTPS URL，最多 9 张；需要本地素材时先调用媒体上传接口，拿到可读 URL 后再写进计划。

创建后先打开 episode-01-plan.json 审阅每个 prompt。每场戏建议写清楚人物动作、景别、镜头运动、环境、时间、声音和不可改变的服装/道具。参考图只是连续性线索，不能保证每一帧都保持同一张脸。

## 2. 计算草稿和成片预算

~~~sh
python -m drama.pipeline \
  --episode 1 --plan episode-01-plan.json --budget
~~~

输出会逐场列出草稿、成片和合计估算。例如 3 场、每场 5 秒、720p 时，草稿 Mini 是 0.855 USD，标准成片是 1.65 USD，合计 2.505 USD；这只按输出秒计算，参考视频秒数和实际重复提交另行记录。

代码也可以直接用于预算检查：

~~~python
from drama.pipeline import build, budget

plan = build(1, ["车站捡钥匙", "走向影院"])
print(budget(plan)["combined_usd"])
~~~

## 3. 生成草稿

先运行草稿阶段。它对每个场景只提交一次，收到 request_id 就马上写进 draft-manifest.json：

~~~sh
python -m drama.pipeline \
  --episode 1 --plan episode-01-plan.json \
  --phase draft --output-dir .drama/episode-01
~~~

生命周期是 POST /v1/videos/generations，随后 GET /v1/videos/{request_id}，只有状态 done 才 GET /v1/videos/{request_id}/content。接受的状态只有 pending、done、failed、expired；遇到未知状态会停止。进程中断后重新运行同一个计划和目录，会复用已有 request_id。POST 超时但没有 ID 时不要盲目重提，先查服务端或账户记录。

manifest 每次变更都经过临时文件、fsync 和原子替换。下载文件也用临时文件和硬链接发布，不能覆盖已有文件。429 或 503 由服务端返回时，保留正文和 Retry-After；本流程不会把一次不确定的生成复制成第二次。

## 4. 人工审核，再批准成片

草稿完成后逐个观看 clips/scene-XX.mp4。批准命令会校验草稿状态、文件存在性和当前 prompt 的指纹；如果计划被改过，旧审核自动失效：

~~~sh
python -m drama.pipeline \
  --episode 1 --plan episode-01-plan.json \
  --approve scene-01 scene-02 \
  --note "动作清楚，人物服装和画面方向连续" \
  --output-dir .drama/episode-01
~~~

每次审核备注要说明镜头可读性、连续性和需要保留的要求。不要用一句“看起来不错”批准整集，也不要在没有看到草稿的情况下把 approved 写进 JSON。

## 5. 渲染成片并合成

只有 approvals.json 中每个场景都有匹配的草稿和成片请求指纹，才允许 final 阶段：

~~~sh
python -m drama.pipeline \
  --episode 1 --plan episode-01-plan.json \
  --phase final --output-dir .drama/episode-01 \
  --output episode-01.mp4
~~~

成片阶段使用标准 seedance-2.0，下载到 final/scene-XX.mp4，然后以稳定场景顺序生成 ffconcat 清单并执行 ffmpeg。输出是 H.264、yuv420p、AAC；已有 episode-01.mp4 时命令会拒绝覆盖。保留原始片段，检查画面、对白、声音和片尾后再清理。

下面是等价的单镜头 cURL 生命周期，便于排查 runner：

~~~sh
BASE=https://argolink.io/v1
AUTH="Authorization: Bearer $VIDEO_API_KEY"
JOB=$(curl --fail-with-body -sS "$BASE/videos/generations" \
  -H "$AUTH" -H 'User-Agent: seedance-2-0-short-drama-workflow/1.0' \
  -H 'Content-Type: application/json' \
  -d '{"model":"seedance-2.0","prompt":"小雨走进旧影院","duration":5,"resolution":"720p","aspect_ratio":"16:9"}')
ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])' <<< "$JOB")
curl --fail-with-body -sS "$BASE/videos/$ID" \
  -H "$AUTH" -H 'User-Agent: seedance-2-0-short-drama-workflow/1.0'
curl --fail-with-body -sS "$BASE/videos/$ID/content" \
  -H "$AUTH" -H 'User-Agent: seedance-2-0-short-drama-workflow/1.0' -o scene.mp4
~~~

第二条 GET 只有返回 done 后才能运行，示例故意没有替你隐藏轮询状态。

## 规格和边界

三种变体都接受 4–15 秒整数和一次一条输出。文生与参考模式使用 21:9、16:9、4:3、1:1、3:4、9:16；首帧/首尾帧可以用 adaptive。参考模式最多 9 张图片、3 个视频、3 个音频，共 15 个素材；参考视频和音频每段 2–15 秒、每类合计 15 秒。标准模型另有 1080p 与 4K。每个场景的 prompt 不能超过 40,000 UTF-8 字节。

对比外部服务时只使用其当天公开页面：[Evolink 的 Seedance 2.5 页面](https://evolink.ai/seedance-2-5)和 [fal.ai 的 Seedance 2.5 页面](https://fal.ai/models/bytedance/seedance-2.5/text-to-video)的计费字段与 Seedance 2.0 不同，因此本仓库不把它们的价格抄进 2.0 成本表。

## 错误处理和测试

400 通常是字段、比例、时长或素材错误；401 是 key；402 是余额或计费；404 是未知 ID；409 是尚未生成完成；424 是任务失败；429/503 是临时压力。保留 response body、request_id 和 manifest，不要吞异常。

~~~sh
python -m unittest discover -s tests -v
python -m py_compile drama/pipeline.py
~~~

测试使用假的 HTTP 响应，不访问生产接口，也不提交计费任务；覆盖模型选择、预算、鉴权头、状态校验、断点 manifest 和成片阶段审核门槛。
