# Q4 检测层：局部凸包证书与联合优化（方法二原型）

日期：2026-09-13  
代码：`framework/src/mathmodel2026b/joint_layout.py`  
测试：`framework/tests/test_joint_layout.py`

---

## 1. 数学判据

目标域

\[
\Omega=\{x\in\mathbb R^2:\|x\|\le1800\}.
\]

检测点集 \(P=\{q_1,\dots,q_m\}\)。对源位置 \(x\)、朝向 \(n\)，可见半圆盘

\[
K(x,n)=\{y:\|y-x\|\le1000,\ n^\top(y-x)\ge0\}.
\]

发现完备性：

\[
\forall x\in\Omega,\ \forall n\in\mathbb S^1,\quad
P\cap K(x,n)\neq\varnothing.
\]

固定 \(x\) 时，令

\[
S_x=P\cap \overline B(x,1000).
\]

等价的局部凸包判据：

\[
\forall x\in\Omega,\quad
x\in\operatorname{conv}(S_x).
\]

正确性：

* 若 \(x\in\operatorname{conv}(S_x)\)，但某个 \(n\) 使所有 \(q\in S_x\) 都有
  \(n^\top(q-x)<0\)，则整个凸包在严格负半空间，与 \(x\) 的凸组合为零矛盾；
* 反之，若 \(x\notin\operatorname{conv}(S_x)\)，分离超平面给出 \(n\)，使
  \(n^\top(q-x)<0\) 对所有 \(q\in S_x\)，该朝向可以避开全部检测点。

**一个需要修正的表述**：“连续路径完备 ⇒ 存在有限子集完备”并不是一般拓扑事实。
闭合覆盖的紧性不能直接推出有限子覆盖；实际题目执行的是有限检测点，所以
`joint_layout.py` 直接从有限点集 \(P\) 出发，把局部凸包式当作硬证书。

---

## 2. 连续域证书

对任意点集，检查连续域 \(\Omega\) 上所有 \(x\) 很难。代码使用正方形单元 +
充分条件：

1. 把包围盒切成正方单元，并裁剪到 \(\Omega\) 的外接多边形；
2. 对单元 \(C\)，取

   \[
   S_C=\{p_i:\max_{v\in\operatorname{vert}(C)}\|p_i-v\|\le1000-\eta\};
   \]

3. 若 \(\operatorname{vert}(C)\subset\operatorname{conv}(S_C)\)，则

   \[
   C\subseteq\operatorname{conv}(S_C)\subseteq\operatorname{conv}(S_x),
   \quad\forall x\in C.
   \]

   第一个包含来自凸性；第二个包含来自：每个 \(p_i\in S_C\) 对单元内任意
   \(x\) 都在 \(1000-\eta\) 内。
4. 若单元太大导致不成立，就递归四分，直到 `min_cell_m`；仍未通过则返回
   `ok=False`，优化器必须回退或继续细分。

所以：

* `ok=True` 是严格证明；
* `ok=False` 只是“当前证书无法证明”，不一定代表几何真的不完备。

---

## 3. 方法二联合优化

`JointLayoutOptimizer` 实现：

1. 初始点集 = 方法一的轴向三角网格（间距 950m）；
2. 2-opt 优化开放路径顺序；
3. 在 **严格连续证书** 约束下：
   * 尝试删除冗余点；
   * 对每个点尝试朝质心、相邻路径点、随机方向移动；
   * 接受条件：目标函数下降；
4. 固定点集后再用 2-opt 调整顺序；
5. 每轮结束再次复核连续证书；失败回退到上一份已证明可行的快照。

目标函数默认是

\[
\frac{L}{5}+c_{\text{measure}}\cdot m,
\]

其中 \(L\) 是开放路径长度，\(c_{\text{measure}}\) 由
`measure_cost_per_stop_s` 控制。把已发现源的清除点插入路径时，只需在顺序优化
阶段把清除点作为必经节点加入路线即可，完备性证书只依赖检测点集合 \(P\)。

---

## 4. 当前原型验证

以 31 点轴向三角网格为初始点集，使用 Hamiltonian 开放链顺序：

| 方案 | 点数 | 路径长度 | 连续证书 |
|---|---:|---:|---|
| 方法一轴向网格 | 31 | 28500m | 通过 |
| 严格联合微调（1 轮，概念验证） | 31 | 26997m | 通过 |

路径长度下降约 5.3%。这个结果来自一次概念验证，不是针对全部随机案例的稳健收益；
正式使用前应在多个初始顺序、多个随机种子下做配对实验，始终把全清与连续证书
作为硬前提。

---

## 5. SOCP 扩展接口

`joint_layout.socp_refine()` 提供固定顺序、固定加权组合的 SOCP 精调接口：

* 对每个采样点 \(x_j\)，用当前点集求一组近邻权重 \(\lambda_{ij}\)；
* 固定权重后，约束

  \[
  \sum_i \lambda_{ij}q_i=x_j,\qquad
  \|q_i-x_j\|\le1000-\eta\quad(\lambda_{ij}>0),
  \]

  对 \(q_i\) 是凸的；
* 目标为路径长度 + 正则项，是 SOCP；
* 每轮求解后用新点集重算权重并重复。

该函数依赖 `cvxpy`，没有安装时不影响本地坐标下降优化器。后续如果要做真正
的大规模联合优化，建议把局部搜索替换成这条 SOCP 外循环。
