# OmniDrones → PX4 单机 Sim2Real

首版用机型档案生成 OmniDrones 刚体与旋翼模型，以及 PX4/Gazebo 四旋翼模型。OmniDrones 运行原生物理闭环，支持从示例基线控制器收集数据并训练 TorchScript 策略，也可以加载符合接口的自有策略。部署包在机载电脑运行，向 PX4 发送速度设定值。

开发工作台请参阅[合作开发指南](CONTRIBUTING.md)。

## 组装和测试人员

工作台与上传资料保存在 `ubuntu-server:~/sim2real`。有该服务器 SSH 权限的合作者可在自己的电脑上运行下列命令，打开 [实验工作台](http://127.0.0.1:18765/)。保持 SSH 终端打开。

```bash
ssh -N -L 18765:127.0.0.1:8000 ubuntu-server
```

- **机型档案**可录入或导入 YAML。称量含电池的整机，旋翼按 PX4 功能电机 1 至 4 排列，以机头方向为 FLU 的 X 正方向。参数来源里标记哪些值是估计。变更硬件或标定值时保存新版本，已有试飞继续关联旧版本。
- **飞行日志**需要机型版本、试飞科目、实际定位来源、起飞电压和 PX4 `.ulg`。机载指令 CSV 和独立参考轨迹 CSV 可后续用于标定与验收。实验室无动捕时选择 VIO 或光流加测距。用同一套 VIO 或光流融合结果做对比时，报告将位置指标标为 PX4 估计；没有独立参考时无法量化绝对位置误差。建议的实飞科目是固定载荷下的悬停、三轴速度阶跃和固定轨迹。
- 工作台中的「已上传独立参考」只表示收到了文件。分析时须把该轨迹变换到 PX4 本地 ENU 并对齐时间。固定航向参考采用本地 ENU 的零航向。

## 启动与存储

```bash
cd ~/sim2real
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt gunicorn
.venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 2 --threads 4 --timeout 300 'sim2real.web:create_app()'
```

默认数据目录为 `workspace/`，可通过 `SIM2REAL_DATA` 指定。机型目录包含 `profile.yaml`、可选 PX4 快照和 `assets/`，试飞目录包含原始上传文件与 `manifest.json`。如果实验室已有 HTTPS 反向代理，可由管理员按实验室账号配置访问，也可用 `SIM2REAL_WEB_PASSWORD` 作为服务的共享密码。当前部署监听回环地址，通过 SSH 转发访问。

## 训练、验证和部署

在服务器项目根目录运行。示例机型只用于仿真；示例数据及推进参数均为初始估计。

```bash
OMNI_PY=~/miniforge3/envs/omnidrones-fegru/bin/python
$OMNI_PY -m sim2real assets profiles/example.yaml build/example
$OMNI_PY -m sim2real.omni profiles/example.yaml build/run-01 --seconds 24 --train
$OMNI_PY -m sim2real.omni profiles/example.yaml build/run-01-eval --actor build/run-01/actor.ts
```

默认训练是行为克隆，学习 OmniDrones 中的限速比例基线。现有模型可用 `python -m sim2real export profile.yaml bundle --actor actor.ts` 导出。输入为 `[N, 11]` 的 `float32`，按 `metadata.json` 的顺序和尺度编码位置、速度、航向正余弦与相对参考点。输出为 `[N, 3]` 的 ENU 速度 m/s。省略 `--actor` 可制作比例基线部署包，不依赖机载 Torch。若需在不同 Python 或架构运行 TorchScript，使用与导出版本兼容的 PyTorch 包。

在安装了 PX4、Gazebo、ROS 2、与 PX4 匹配的 `px4_msgs` 和 Micro XRCE-DDS Agent 的主机上执行下列验证。先选与实机固件相同的 PX4 版本，核对消息字段和话题。Gazebo 模型发送 IMU、磁力计、气压计和 GNSS；该 SITL 路径用于验证 PX4 控制链和部署包。VIO 或光流的相机、地面纹理、估计质量和漂移需要用目标定位硬件单独验证。OmniDrones 从机型档案采用定位更新率、延迟和白噪声近似。

```bash
python -m sim2real.sitl profiles/example.yaml ~/PX4-Autopilot build/sitl-01
MicroXRCEAgent udp4 -p 8888
```

在另一终端加载该 ROS 2 环境并进入部署包目录。默认命令只记录推理结果；验证发布时需显式添加 `--execute --sitl`。实飞省略 `--sitl` 并使用已确认的真实档案。

```bash
python -m sim2real.px4 . --log trial-shadow.csv
python -m sim2real.reference --hold 0 0 1.5
```

悬停示例参考点是 PX4 本地 ENU 坐标，应按试飞场地与估计原点核对。轨迹使用 `python -m sim2real.reference --csv trajectory.csv`，列为 `t_s,ref_x,ref_y,ref_z`。操作员负责解锁和起飞，在稳定悬停及航向有效后切入 Offboard，并准备接管。验证时分别中断定位、停止参考发布和退出机载进程，检查心跳停发后 PX4 实际处置。机载节点发现 PX4 原点或航向重置后，要求核对参考坐标并重启节点。核对实机的载荷、电压范围、Offboard 失联参数与手动接管后，再运行低速试飞。

## 无动捕标定与验收

1. 将 ULog 和机载指令 CSV 保留在同一试飞记录。机载 CSV 中的时间是 ROS 时钟，ULog 的时间是飞控时钟。用同一次试飞中明确对应的时间点或同步记录确定 `PX4 时间 - ROS 时间`，再按所选飞行窗口转换。实飞窗口需要包含完整动作前后的响应，并避开估计器重置。对阶跃标定，记录稳定的阶跃前后速度指令和电压。
2. 阶跃工具使用基线部署包，在控制节点添加 `--velocity-calibration`，用 `python -m sim2real.reference --velocity-step 0.2 0 0` 发布限时的单轴速度指令，先在 SITL 确认方向和时序。`python -m sim2real normalize flight.ulg commands.csv run.csv --start-px4-s START --end-px4-s END --command-offset-s OFFSET` 生成时间已对齐的 ENU CSV。`START`、`END` 和 `OFFSET` 都以秒为单位。原始网页上传可不带机载指令，分析命令需要同期的指令记录。
3. `python -m sim2real fit profile.yaml calibration.json --step-x x.csv --step-y y.csv --step-z z.csv` 拟合闭环增益、响应时间和有效延迟。可重复各个 `--step-*` 参数以计算试飞间范围。用 `--calibration calibration.json` 注入训练，并在不同训练回合使用 `--randomize --seed N`。台架推力曲线和压降仍保留估计状态。
4. 用 `sim2real.omni` 的 `--replay run.csv` 重放独立试飞的速度指令。对比 PX4 估计时使用 `estimate.csv`，有独立参考时使用记录真实物理状态的 `rollout.csv`。保留标定前和标定后的两组仿真结果，用同一独立试飞集比较。轨迹策略验收用 `--reference-replay --actor actor.ts`，让同一策略跟踪所录制的参考。`pairs.json` 是数组，每项包含 `scenario`、`real` 和 `sim` 文件路径，以及可选的 `reference` 路径。独立参考 CSV 的必需列为 `t_s,x,y,z`。科目为 `hover`、`step-x`、`step-y`、`step-z`、`trajectory`。
5. `python -m sim2real evaluate profile.yaml pairs.json report.json --calibration calibration.json` 输出悬停和轨迹 RMSE、10–90% 阶跃上升时间、电压窗口验证和资料覆盖情况。当前门槛为三个轴各自的上升时间差最多 20%，轨迹 RMSE 差最多 0.15 m。每科目至少三次，每个试飞的验收记录须与标定数据独立。请把 `normalize` 生成的 `.meta.json` 与 CSV 保存在一起，分析会用原始 ULog 哈希拦截从同次飞行裁切出的重复记录。报告同时保留实机误差绝对值和依据字段。

## 测试

```bash
python -m unittest discover -s tests -v
```

SITL 集成测试在 Linux 或 WSL 的 ROS 2 环境中运行。脚本启动自己的 Gazebo 和 PX4 进程，使用单独的 Gazebo 分区、ROS 域和 DDS 端口，结束后退出这些进程。

```bash
python -m tests.sitl_smoke ~/PX4-Autopilot build/sitl-smoke --bundle build/run-01/bundle
```
