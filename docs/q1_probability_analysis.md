# 问题 1：直径圆覆盖概率与期望的几何概率分析

> 本文档是“决策探索”性质的半理论、半数值分析。  
> 目标不是给出完整的闭式概率公式，而是把问题严格形式化，给出可检验的蒙特卡洛估计，并说明模型假设对结论的巨大影响。  
> 配套脚本：`script/mc_q1_probability.py`  
> 配套 C++/Python 几何内核：`cpp/geom.hpp`、`cpp/bindings.cpp`

---

## 0. 结论摘要

设目标区域为半径 \(R=1800\,\mathrm m\) 的圆盘。  
在最自然的“等面积独立均匀”模型下：

- 目标点 \(G\) 在圆盘内等面积均匀；
- 每个观测点 \(P_i\) 也在同一个圆盘内等面积均匀；
- 观测误差
  \[
  \delta_i\sim \mathrm{Uniform}[-1^\circ,1^\circ];
  \]
- 定位区域
  \[
  \mathcal R = D(0,R)\cap \bigcap_i W(P_i,\theta_i+\delta_i),
  \]
  其中 \(W(P,\theta)\) 是顶点为 \(P\)、方向为 \(\theta\)、半角 \(1^\circ\) 的楔形区域。

取 \(k=2,3,4\) 个观测点时，用 20000 次蒙特卡洛得到：

| 观测点数 \(k\) | 直径圆能覆盖概率 | 直径圆不能覆盖概率 | 最小圆半径 \(\le20\mathrm m\) 概率 | \(\mathbb E[D]\) | \(\mathbb E[R_{\min}]\) |
|---:|---:|---:|---:|---:|---:|
| 2 | 96.21% | 3.79% | 1.46% | 295.812 | 147.921 |
| 3 | 86.25% | 13.75% | 8.49% | 122.416 | 61.252 |
| 4 | 79.20% | 20.80% | 20.57% | 77.306 | 38.717 |

这里的“直径圆能覆盖”等价于 Welzl 最小包围圆是**二点基**；  
“直径圆不能覆盖”等价于 Welzl 最小包围圆是**三点基**。

观察点分布影响很大。若观测点不再是全域均匀，而是围绕目标点以 800–1500 m 环形放置，则：

| 模型 | \(k=2\) | \(k=3\) | \(k=4\) |
|---|---:|---:|---:|
| 全域均匀 `disk` | 96.21% | 86.25% | 79.20% |
| 目标中心环形 `ring` | 98.05% | 79.08% | 67.78% |
| 目标周围环形 `ring_offset` | 98.97% | 80.20% | 68.43% |

因此，**必须先固定观测点采样模型，概率才有意义**。之前得到约 60% 的结果，很可能对应的是环形/局部观测策略下、\(k\ge4\) 或 \(k\ge5\) 的某种设置，而不是“全域均匀 + \(k=2,3,4\)”。

---

## 1. 确定性几何判据

设定位区域 \(\mathcal R\) 为非空有界凸多边形。  
令
\[
D = \operatorname{diam}(\mathcal R)
= \max_{p,q\in\mathcal R}\|p-q\|,
\]
并令 \(A,B\in\mathcal R\) 为一对直径端点：
\[
\|A-B\|=D.
\]

以 \(AB\) 为直径的圆为
\[
\Gamma_{AB}
=
\left\{
Q:
(Q-A)\cdot(Q-B)\le 0
\right\}.
\]

对任意 \(Q\in\mathbb R^2\)：
\[
Q\in\Gamma_{AB}
\iff
\left\|Q-\frac{A+B}{2}\right\|
\le
\frac{D}{2}.
\]

由于 \(\mathcal R\) 是凸多边形，检查所有顶点即可：
\[
\mathcal R\subseteq\Gamma_{AB}
\iff
\max_{V\in\operatorname{Vert}(\mathcal R)}
(V-A)\cdot(V-B)\le 0.
\]

设 \(\mathcal R\) 的最小包围圆为 \((C,R_{\min})\)。则
\[
\frac D2 \le R_{\min}\le \frac{D}{\sqrt 3}.
\]

进一步：

\[
\boxed{
\text{直径圆覆盖 }\mathcal R
\iff
2R_{\min}=D
}
\]

这是因为：
- 若 \(2R_{\min}=D\)，最小包围圆半径正好等于 \(\frac D2\)，而任意直径端点 \(A,B\) 在该圆上相距 \(D\)，故它们必须是圆的对径点，直径圆就是最小包围圆；
- 若 \(2R_{\min}>D\)，任何半径为 \(\frac D2\) 的圆都不可能包含全部 \(\mathcal R\)。

Welzl 算法返回的基点数：

- 基点数为 2：最小圆由一对对径点确定，直径圆覆盖；
- 基点数为 3：最小圆由不共线三点外接圆确定，通常直径圆不覆盖。

---

## 2. 随机模型与精确概率积分

### 2.1 观测模型

目标点
\[
G\sim \mathrm{Uniform}(D(0,R)).
\]

观测点
\[
P_i \sim \mathrm{Uniform}(D(0,R)),
\qquad i=1,\dots,k,
\]
独立同分布。

真实方位角：
\[
\theta_i^{\mathrm{true}}
=
\angle(P_i,G)
=
\operatorname{atan2}(G_y-P_{i,y},\,G_x-P_{i,x}).
\]

测量方位角：
\[
\theta_i
=
\theta_i^{\mathrm{true}}+\delta_i,
\qquad
\delta_i\sim \mathrm{Uniform}[-1^\circ,1^\circ].
\]

单次观测楔形：
\[
W_i
=
\left\{
Q:
\left|
\operatorname{wrap}(\angle(P_i,Q)-\theta_i)
\right|
\le 1^\circ
\right\}.
\]

定位区域：
\[
\mathcal R
=
D(0,R)
\cap
\bigcap_{i=1}^k W_i.
\]

### 2.2 精确事件概率

“直径圆覆盖”是一个事件：
\[
\mathcal C
=
\left\{
\mathcal R\subseteq\Gamma_{AB}
\right\},
\]
其中 \(A,B\) 是随机凸区域 \(\mathcal R\) 的直径端点。

于是
\[
\Pr(\mathcal C)
=
\int
\mathbf 1_{\mathcal C}
\;
\mathrm d\Pr(G)
\prod_{i=1}^{k}\mathrm d\Pr(P_i)
\prod_{i=1}^{k}\mathrm d\Pr(\delta_i).
\]

写开就是：
\[
\Pr(\mathcal C)
=
\int_{D(0,R)^{k+1}}
\int_{[-1^\circ,1^\circ]^k}
\mathbf 1
\left[
\max_{V\in\operatorname{Vert}(\mathcal R(G,P_{1:k},\delta_{1:k}))}
(V-A)\cdot(V-B)\le 0
\right]
\]
\[
\times
\frac{\mathrm dG}{\pi R^2}
\prod_{i=1}^k \frac{\mathrm dP_i}{\pi R^2}
\prod_{i=1}^k \frac{\mathrm d\delta_i}{2^\circ}.
\]

这是一个 \(2(k+1)+k=3k+2\) 维积分，而且被积事件依赖：

- 凸区域 \(\mathcal R\) 的顶点组合；
- 直径端点 \(A,B\) 的组合；
- 顶点的顺序与激活约束。

因此除了极少数退化情形，不存在简单闭式。

---

## 3. 为什么闭式推导困难

困难主要来自三层随机性嵌套：

1. **观测点位置随机**  
   \(P_i\) 决定楔形顶点与真实方位角。

2. **示向角误差随机**  
   \(\delta_i\) 决定每个楔形绕真实方向的随机旋转，影响区域宽度和位置。

3. **区域直径的随机组合**  
   直径端点 \(A,B\) 是所有顶点对的 argmax，属于随机凸多边形的极值组合问题。

即使固定 \(G,P_i,\delta_i\)，判定函数仍然是一个非光滑的分段函数：
\[
\mathbf 1_{\mathcal C}
=
\prod_{V\in\operatorname{Vert}(\mathcal R)}
\mathbf 1\left[(V-A)\cdot(V-B)\le 0\right].
\]

当 \(k\) 增大时，\(\mathcal R\) 的顶点数上界为 \(O(k)\)，其形状接近随机凸多边形。  
随机凸多边形的最小包围圆基点数分布属于随机几何中的困难问题，通常只能数值研究。

---

## 4. 可用的近似与直观解释

### 4.1 小角度条带近似

因为楔形半角只有 \(1^\circ\)，在可行域尺度上，每个楔形可以局部近似为一条带状约束：
\[
|n_i\cdot(Q-G)|\lesssim d_i\tan 1^\circ,
\]
其中 \(n_i\) 是垂直于 \(P_iG\) 的单位向量，\(d_i=\|P_i-G\|\)。

于是
\[
\mathcal R
\approx
D(0,R)
\cap
\bigcap_i
\left\{
Q:
|n_i\cdot(Q-G)|\le h_i
\right\}.
\]

若这些条带关于 \(G\) 近似对称，则 \(\mathcal R\) 近似中心对称。  
对于任意中心对称凸体，直径圆一定覆盖它：

> 若 \(K\) 关于原点中心对称，直径 \(D=2\max_{x\in K}\|x\|\)，  
> 则 \(K\subseteq B(0,D/2)\)，即以任意长度 \(D\) 的对径段为直径的圆都覆盖 \(K\)。

这解释了为什么 \(k=2\) 时覆盖概率非常高：
两个 \(2^\circ\) 楔形的相交区域近似一个细长平行四边形，而平行四边形是中心对称的。

### 4.2 \(k=2\) 的平行四边形近似

两个小角度楔形的交集近似一个平行四边形。  
对任意平行四边形，最长对角线为直径的圆总覆盖整个平行四边形。

因此 \(k=2\) 的理论近似概率为
\[
\Pr(\mathcal C\mid k=2)\approx 1.
\]

实际蒙卡得到 96.21%，剩余的 3.79% 来自：

- 楔形不是严格的平行条带；
- 目标圆边界参与了裁剪；
- 两个楔形方向过度接近或退化。

### 4.3 \(k\ge3\) 的直观

随着观测点增多，\(\mathcal R\) 被更多方向的条带切割，形状更接近任意的随机凸多边形，中心对称性下降。  
因此：

\[
\Pr(\text{二点基})
\downarrow
\quad\text{随 }k\text{ 增大}.
\]

这与数值结果一致：
\[
96.21\% \to 86.25\% \to 79.20\%
\quad (k=2,3,4).
\]

同时，区域越来越小：
\[
\mathbb E[D]:\quad 295.8\to122.4\to77.3,
\]
\[
\mathbb E[R_{\min}]:\quad 147.9\to61.3\to38.7.
\]

这说明“直径圆是否覆盖”与“区域是否小到可 20 m 清除”是两个不同问题：

- 直径圆覆盖概率较高；
- 但 \(R_{\min}\le20\mathrm m\) 的概率仍较低：
  \[
  1.46\%\to8.49\%\to20.57\%.
  \]

---

## 5. 蒙特卡洛估计

用 \(N\) 次独立抽样估计
\[
p=\Pr(\mathcal C).
\]
估计量
\[
\hat p_N=\frac1N\sum_{j=1}^N \mathbf 1_{\mathcal C^{(j)}}.
\]
标准误
\[
\widehat{\mathrm{se}}(\hat p_N)
=
\sqrt{\frac{\hat p_N(1-\hat p_N)}{N}}.
\]

在 \(N=20000\) 时，即使 \(p=0.5\)，
\[
\widehat{\mathrm{se}}\le \sqrt{\frac{0.25}{20000}}\approx0.354\%.
\]
因此表中概率的最后一位小数有约 \(0.1\%\sim0.3\%\) 的数值误差。

期望估计：
\[
\widehat{\mathbb E[D]}
=
\frac1N\sum_{j=1}^N D^{(j)},
\qquad
\widehat{\mathbb E[R_{\min}]}
=
\frac1N\sum_{j=1}^N R_{\min}^{(j)}.
\]

---

## 6. 数值结果

### 6.1 模型一：目标与观测点同圆盘等面积均匀

20000 次样本：

| \(k\) | \(P(\text{直径圆覆盖})\) | \(P(\text{三点基})\) | \(P(R_{\min}\le20)\) | \(\mathbb E[D]\) | \(\mathbb E[R_{\min}]\) |
|---:|---:|---:|---:|---:|---:|
| 2 | 96.21% | 3.79% | 1.46% | 295.812 | 147.921 |
| 3 | 86.25% | 13.75% | 8.49% | 122.416 | 61.252 |
| 4 | 79.20% | 20.80% | 20.57% | 77.306 | 38.717 |

### 6.2 策略敏感性

| 模型 | \(k\) | \(P(\text{覆盖})\) | \(P(\text{三点基})\) | \(\mathbb E[D]\) | \(\mathbb E[R_{\min}]\) |
|---|---:|---:|---:|---:|---:|
| 全域均匀 | 2 | 96.21% | 3.79% | 295.812 | 147.921 |
| 全域均匀 | 3 | 86.25% | 13.75% | 122.416 | 61.252 |
| 全域均匀 | 4 | 79.20% | 20.80% | 77.306 | 38.717 |
| 目标中心环形 | 2 | 98.05% | 1.95% | 276.943 | 138.472 |
| 目标中心环形 | 3 | 79.08% | 20.92% | 92.848 | 46.508 |
| 目标中心环形 | 4 | 67.78% | 32.22% | 60.633 | 30.447 |
| 目标周围环形 | 2 | 98.97% | 1.03% | 288.578 | 144.289 |
| 目标周围环形 | 3 | 80.20% | 19.79% | 85.902 | 43.022 |
| 目标周围环形 | 4 | 68.43% | 31.57% | 56.052 | 28.138 |

可以看到：

- \(k=2\)：几乎总是二点基，直径圆覆盖；
- \(k=3,4\)：三点基概率明显上升；
- 环形策略在 \(k\ge3\) 时覆盖概率反而更低，说明“观测点分布越对称”不必然使直径圆覆盖更容易；
- 但环形策略往往更快缩小区域，因此 \(20\mathrm m\) 可清除概率可能上升。

这正是决策问题需要权衡的：
\[
\text{信息增益}
\quad\text{vs.}\quad
\text{直径圆覆盖性质}
\quad\text{vs.}\quad
\text{最小圆半径收缩速度}.
\]

---

## 7. Python 复现

配套脚本：

```bash
./cpp/build.sh

PYTHONPATH=cpp python3 script/mc_q1_probability.py \
    --models disk ring ring_offset \
    --ks 2 3 4 \
    --samples 20000
```

输出 Markdown 表格。核心调用：

```python
region = geom_cpp.build_region(observations, bound_radius=1800.0, bound_sides=128)
diameter = geom_cpp.rotating_calipers_diameter(region)
circle = geom_cpp.welzl_min_enclosing_circle(region, seed=123)

cover = geom_cpp.diameter_circle_covers(region, diameter)
clear20 = geom_cpp.can_clear(circle, 20.0)
```

随机观测生成：

```python
true_bearing = wrap360(math.degrees(math.atan2(target.y - p.y, target.x - p.x)))
noise = rng.uniform(-1.0, 1.0)
bearing = wrap360(true_bearing + noise)
```

---

## 8. 形式化目标（可选，Lean 4）

如果要形式化，最有价值的是确定性的几何判据，而不是高维概率积分。

目标定理可以写成：

```lean
-- 伪 Lean 4 目标：在二维欧氏空间中，
-- V 落在以 A、B 为直径的闭圆盘内
-- 当且仅当 (V-A) 与 (V-B) 的内积 <= 0。

theorem mem_diameter_disk_iff
    (A B V : EuclideanSpace ℝ (Fin 2)) :
    dist V ((A + B) / 2) ≤ dist A B / 2
      ↔
    inner ℝ (V - A) (V - B) ≤ 0 := by
  sorry
```

进一步可以形式化：

```lean
theorem diameter_circle_covers_iff_two_point_basis
    (K : Set (EuclideanSpace ℝ (Fin 2)))
    (hK : Convex ℝ K) :
    -- 若 K 的最小包围圆由一对对径点 A,B 确定，
    -- 则以 A,B 为直径的圆覆盖 K
    ...
```

概率部分不建议形式化：它依赖连续测度、随机凸多边形顶点组合和高维积分，形式化成本远高于本题决策问题的收益。

---

## 9. 结论

1. 在“同圆盘等面积均匀 + 角度误差 \(\pm1^\circ\)”模型下，直径圆覆盖概率随观测点数增加而下降：
   \[
   k=2:\ 96.21\%,\quad
   k=3:\ 86.25\%,\quad
   k=4:\ 79.20\%.
   \]

2. 对应“三点基”概率为：
   \[
   3.79\%,\quad 13.75\%,\quad 20.80\%.
   \]

3. 观测点分布会显著改变这些概率。环形/局部策略下，\(k=4\) 时覆盖概率可降到约 \(68\%\)。

4. 直径圆覆盖概率高，不代表 \(20\mathrm m\) 可清除概率高。后者需要最小包围圆半径本身足够小，需要更多观测或更优的选点策略。

5. 因此，问题 1 的概率结论应作为策略探索的“参考量”，而不是最终决策的唯一依据。
