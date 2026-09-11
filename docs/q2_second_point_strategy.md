# 问题 2：第二检测点选择与候选区域

> 本文档延续 `docs/q1_probability_analysis.md` 的风格：先建立概率模型，再写精确积分表达式，最后做确定性数值求积。
> 这里的“精确”有明确含义：概率积分写成连续形式后，用 Gauss-Legendre 求积离散；每个样本点对应的定位区域由半平面/圆盘求交精确构造，而不是蒙特卡洛抽样。
> 配套脚本：`script/q2_second_point_quadrature.py`
> 几何内核：`cpp/geom.hpp`、`cpp/bindings.cpp`

---

## 0. 结论摘要

记目标区域为
\[
\Omega=D(0,R_0),\qquad R_0=1800\,\mathrm m,
\]
有效接收半径
\[
\rho\sim \mathrm{Uniform}[1000,1500]\quad(\text{单位:m}).
\]

若第一检测点可以在观测前选定，推荐取
\[
S_1=(0,0).
\]
在源点先验均匀的假设下，这是使第一次成功检测概率最大的位置；数值上
\[
\Pr(\text{第一次成功}\mid S_1=O)\approx 48.87\%.
\]

设第一次成功检测得到示向度 \(\bar\theta_1\)。在局部坐标中令
\[
e(\theta)=(\cos\theta,\sin\theta),\qquad
e_\perp(\theta)=(-\sin\theta,\cos\theta),
\]
并把第一测向线方向作为局部 \(x\) 轴。第二检测点写成
\[
S_2=S_1+x\,e(\bar\theta_1)+y\,e_\perp(\bar\theta_1).
\]
则推荐候选区域为两个关于第一测向线对称的“侧瓣”：
\[
\boxed{
\mathcal C_{\rm cand}
=
\mathcal C_0
\cap
\left\{(x,y): |x-\mu_r|\le \sqrt3\,|y|\right\},
}
\]
其中
\[
\mu_r=\mathbb E[r\mid S_1=O,\bar\theta_1]
\approx 855.26\,\mathrm m
\]
是第一次成功检测条件下源距离的后验均值，而
\[
\mathcal C_0
=
D(0,1000)
\cap
D\!\left(1500e(\bar\theta_1+\varepsilon),1000\right)
\cap
D\!\left(1500e(\bar\theta_1-\varepsilon),1000\right),
\qquad \varepsilon=1^\circ,
\]
是**保证第二次仍能检测到信号**的稳健区域。
直观上，\(\mathcal C_0\) 是环绕第一测向线、距第一点约 \(500\sim1000\,\mathrm m\)、横向最宽约 \(\pm 650\,\mathrm m\) 的透镜形区域；角度条件再挖去靠近测向线的窄带，得到上下两个侧瓣。

在 \(\mathcal C_{\rm cand}\) 中，以下几个点体现了不同权衡（坐标单位为米）：

| 第二点 \((x,y)\) | 定位目标 | \(p_{\det}\) | \(\mathbb E[R_{\min}]\) | \(\mathrm{sd}(R_{\min})\) | \(P(R_{\min}\le20)\) |
|---|---:|---:|---:|---:|---:|
| \((700,450)\) | 最大化一次 \(20\,\mathrm m\) 清除概率 | 1.000000 | 28.474 | 14.557 | 41.03% |
| \((850,520)\) | 综合稳健：保证检测 + 较高一次清除率 | 1.000000 | 25.361 | 8.574 | 35.59% |
| \((860,510)\) | 略偏最小期望误差 | 1.000000 | 25.335 | 8.445 | 34.47% |
| \((900,550)\) | 最小期望误差，允许极小检测风险 | 0.999901 | 25.095 | 7.079 | 28.92% |

若只给一个默认推荐，建议取
\[
\boxed{(x,y)=(850,520)\quad\text{或}\quad(860,510)}
\]
并任选 \(y>0\) 或 \(y<0\) 的一侧。该点在保证第二次检测成功的同时，把期望最小包围圆半径压到约 \(25.4\,\mathrm m\)，且一次 \(20\,\mathrm m\) 区域覆盖概率约 \(35\%\)。

---

## 1. 建模假设与记号

### 1.1 基本设定

- 目标源 \(G\) 在 \(\Omega\) 内等面积均匀；
- 有效接收半径
  \[
  \rho\sim \mathrm{Uniform}[1000,1500];
  \]
- 第一次测向误差
  \[
  \delta_1\sim \mathrm{Uniform}[-1^\circ,1^\circ];
  \]
- 第二次测向误差
  \[
  \delta_2\sim \mathrm{Uniform}[-1^\circ,1^\circ];
  \]
- 第一次检测已经成功，因此在第一点测量的示向度 \(\bar\theta_1\) 已知；
- 第一点、第二点检测的是同一全向源，所以两次检测使用同一个 \(\rho\)。

本文先处理推荐情形 \(S_1=O\)。此时第一测向楔形与目标圆盘的交集不会碰到圆盘边界，因为第一次成功已经给出
\[
d_1=\|G-S_1\|\le \rho\le1500<1800.
\]
所以第一可行集是一个半径被 \(1500\) 截断的 \(2^\circ\) 窄扇形，后续推导最干净。

### 1.2 局部坐标

令
\[
\bar\theta_1=0
\]
（整体旋转不改变度量）。设
\[
G=(r\cos\phi,r\sin\phi),\qquad
|\phi|\le\varepsilon,\quad 0\le r\le1500,
\]
第二点为
\[
S_2=(a,b).
\]
则
\[
d_1=r,\qquad
d_2=\sqrt{(r\cos\phi-a)^2+(r\sin\phi-b)^2}.
\]

真实第二方位角为
\[
\beta_2(G)=\operatorname{atan2}(r\sin\phi-b,\;r\cos\phi-a).
\]
第二次实际测得的示向度为
\[
\bar\theta_2=\beta_2(G)+\delta_2.
\]

---

## 2. 第一点应如何选择

如果允许在第一次观测前选择 \(S_1\)，则对任意 \(G\)，
\[
\Pr(\text{第一次成功}\mid G,\rho)
=\mathbf 1_{\|G-S_1\|\le\rho}.
\]
对 \(\rho\sim\mathrm{Uniform}[1000,1500]\) 积分，得到
\[
q(d)=
\begin{cases}
1,&0\le d\le1000,\\[2mm]
\dfrac{1500-d}{500},&1000<d\le1500,\\[2mm]
0,&d>1500.
\end{cases}
\]
因此
\[
\Pr(\text{第一次成功}\mid S_1)
=
\frac1{\pi R_0^2}
\int_{\Omega}
q(\|G-S_1\|)\,\mathrm dG.
\]
\(q(d)\) 是 \([0,\infty)\) 上的非增径向函数。由重排/对称化可知，对固定的 \(\Omega\)，该积分在圆心 \(S_1=O\) 处最大。于是

\[
\boxed{
\text{若第一点可规划，应尽量选在目标区域中心 }O.
}
\]

数值上
\[
\Pr(\text{第一次成功}\mid S_1=O)
=
\frac{2}{R_0^2}
\int_0^{1500}q(r)r\,\mathrm dr
\approx 0.488683.
\]

这给出了“第一点如何选择”的严格理由。若实际中第一点已被给定且不在中心，下一节同一套积分只需把 \(r\) 的上限换成沿方向 \(\phi\) 到 \(\Omega\) 边界的距离
\[
L(\phi)=\min\left(1500,\;-S_1\cdot u(\phi)+\sqrt{R_0^2-\|S_1\|^2+(S_1\cdot u(\phi))^2}\right),
\]
其中 \(u(\phi)=(\cos\phi,\sin\phi)\) 是在以 \(S_1\) 为原点的局部坐标系中的方向；其余公式不变。

---

## 3. 第一次成功后的后验与第二次检测概率

### 3.1 径向后验权重

这里要特别澄清：\(\rho\) 是最大可被有效接收距离（有效接收半径），不是源到观测点的实际距离。第一次成功只给出
\[
d_1\le \rho,\qquad \rho\le1500,
\]
并不能推出 \(d_1\ge1000\)。数值 \(1000\) 只在“保证第二次也能检测”的最坏情况约束中出现，即要求 \(d_2\le1000\)。

因此给定 \(G\) 的后验权重为
\[
\boxed{
K_1(d_1)
=
\left(1500-\max(1000,d_1)\right)_+ .
}
\]
在 \(S_1=O\)、局部极坐标下，\(d_1=r\)，故
\[
K_1(r)=
\begin{cases}
500,&0\le r\le1000,\\
1500-r,&1000<r\le1500,\\
0,&r>1500.
\end{cases}
\]

由于 \(G\) 在 \(\Omega\) 内均匀，面积元为 \(r\,\mathrm dr\,\mathrm d\phi\)，所以第一次成功、第一次测得 \(\bar\theta_1=0\) 后的后验密度为
\[
p(r,\phi\mid S_1,\bar\theta_1)
=
\frac{K_1(r)\,r}
{2\varepsilon_{\rm rad}\,I_1},
\qquad
I_1=\int_0^{1500}K_1(r)r\,\mathrm dr.
\]
一个有用的量是源距离后验均值
\[
\mu_r
=
\frac{\int_0^{1500}K_1(r)r^2\,\mathrm dr}
{\int_0^{1500}K_1(r)r\,\mathrm dr}
\approx855.263\,\mathrm m.
\]

### 3.2 第二次检测概率

第二次也成功等价于
\[
d_2\le\rho.
\]
由于同一 \(\rho\)，联合权重为
\[
\boxed{
K_{12}(r,d_2)
=
\left(1500-\max(1000,r,d_2)\right)_+ .
}
\]
所以候选第二点 \(S_2=(a,b)\) 的第二次检测概率为
\[
\boxed{
p_{\det}(a,b)
=
\frac{
\displaystyle
\int_{-\varepsilon}^{\varepsilon}
\int_0^{1500}
K_{12}(r,d_2(r,\phi;a,b))
\,r\,\mathrm dr\,\mathrm d\phi
}{
\displaystyle
\int_{-\varepsilon}^{\varepsilon}
\int_0^{1500}
K_1(r)
\,r\,\mathrm dr\,\mathrm d\phi
}.
}
\]
当 \(d_2\le1000\) 对所有可能的 \(G\) 都成立时，有
\[
\max(1000,r,d_2)=\max(1000,r),
\]
于是 \(K_{12}=K_1\)，从而
\[
p_{\det}=1.
\]

---

## 4. 第二次观测后的精确定位区域

### 4.1 楔形与圆盘交集

对点 \(P\) 和示向度 \(\theta\)，定义半角为 \(\varepsilon=1^\circ\) 的楔形
\[
W(P,\theta)
=
\left\{
Q:
\left|\operatorname{wrap}\!\left(\angle(Q-P)-\theta\right)\right|
\le\varepsilon
\right\}.
\]
圆盘记为
\[
D(P,R)=\{Q:\|Q-P\|\le R\}.
\]

第一次成功给出
\[
G\in D(0,1500)\cap W(S_1,\bar\theta_1).
\]
第二次成功又给出
\[
G\in D(S_2,1500)\cap W(S_2,\bar\theta_2).
\]
因此，给定 \(G\)、\(\delta_2\) 后，可能的定位区域是
\[
\boxed{
\mathcal R(G,\delta_2;S_2)
=
D(0,R_0)
\cap D(S_1,1500)
\cap D(S_2,1500)
\cap W(S_1,\bar\theta_1)
\cap W(S_2,\bar\theta_2).
}
\]
其中
\[
\bar\theta_2=\beta_2(G)+\delta_2.
\]
在推荐情形 \(S_1=O\) 下，\(D(0,R_0)\) 被 \(D(0,1500)\) 包含，可以省略；候选点使 \(D(S_2,1500)\) 在可行点附近不活跃，但公式中仍保留。

### 4.2 定位效率评价量

对任意区域泛函 \(f(\mathcal R)\)，例如面积 \(A\)、直径 \(D\)、最小包围圆半径 \(R_{\min}\)、二值量 \(\mathbf 1_{R_{\min}\le20}\)、覆盖数下界 \(N_{20}^{\rm lb}\)，在第二次成功条件下的期望为
\[
\boxed{
\mathbb E[f\mid S_2,\text{第二次成功}]
=
\frac{
\displaystyle
\frac1{2\varepsilon}
\int_{-\varepsilon}^{\varepsilon}
\int_{-\varepsilon}^{\varepsilon}
\int_0^{1500}
K_{12}(r,d_2)
f(\mathcal R(G,\delta_2;S_2))
\,r\,\mathrm dr\,\mathrm d\phi\,\mathrm d\delta_2
}{
\displaystyle
\int_{-\varepsilon}^{\varepsilon}
\int_0^{1500}
K_{12}(r,d_2)
\,r\,\mathrm dr\,\mathrm d\phi
}.
}
\]
其中外层的 \(\delta_2\) 积分是第二次测向误差；分母中的 \(2\varepsilon\) 与分子的均匀密度抵消。

方差为
\[
\operatorname{Var}(f\mid S_2)
=
\mathbb E[f^2\mid S_2]
-
\left(\mathbb E[f\mid S_2]\right)^2.
\]

### 4.3 为什么不能把 \(\mathcal R\) 总当成平行四边形

在极小角度近似下，两个楔形的交集近似为平行四边形，容易得到闭式面积。但当源距离接近 \(1500\,\mathrm m\) 时，第一次成功给出的圆盘 \(D(S_1,1500)\) 会把无限楔形截断，平行四边形近似明显高估区域。因此数值计算中应直接构造
\[
D(S_1,1500)\cap W(S_1,\bar\theta_1)\cap W(S_2,\bar\theta_2)
\]
的多边形，再计算其面积、直径和最小包围圆。小角度公式只用于快速直觉和候选点初筛。

---

## 5. 第二检测点候选区域

### 5.1 保证第二次检测的稳健区域

第一次成功后，可能的源点集合为
\[
\mathcal A_1
=
D(0,1500)\cap W(S_1,\bar\theta_1),
\]
它是一个半径 \(1500\,\mathrm m\)、张角 \(2^\circ\) 的窄扇形。其极值点只有
\[
O,\qquad
G_+=1500e(\bar\theta_1+\varepsilon),\qquad
G_-=1500e(\bar\theta_1-\varepsilon).
\]

要使第二次检测**无论源在 \(\mathcal A_1\) 中何处、无论 \(\rho\) 取 \([1000,1500]\) 中的何值**都成功，至少需要
\[
d_2(G)\le1000,\qquad \forall G\in\mathcal A_1.
\]
由于 \(d_2(G)=\|S_2-G\|\) 是凸函数，在凸紧集 \(\mathcal A_1\) 上的最大值一定在极值点处取得，因此保证区域为
\[
\boxed{
\mathcal C_0
=
D(O,1000)
\cap
D(G_+,1000)
\cap
D(G_-,1000).
}
\]
在局部坐标 \(S_2=(a,b)\)、\(\bar\theta_1=0\) 下，它等价于
\[
\begin{cases}
a^2+b^2\le1000^2,\\[1mm]
(a-1500\cos\varepsilon)^2+(b-1500\sin\varepsilon)^2\le1000^2,\\[1mm]
(a-1500\cos\varepsilon)^2+(b+1500\sin\varepsilon)^2\le1000^2.
\end{cases}
\]
由于 \(\varepsilon=1^\circ\)，\(G_+\) 和 \(G_-\) 非常接近，\(\mathcal C_0\) 约为
\[
B(0,1000)\cap B(1500e(\bar\theta_1),1000)
\]
这个透镜，再被 \(G_\pm\) 两个圆盘稍微切削。数值范围约为
\[
500\lesssim a\lesssim1000,\qquad |b|\lesssim648.
\]

### 5.2 避免差条件交会

若 \(S_2\) 落在第一测向线附近，则两条测向线几乎平行，定位区域会沿测向线拉得很长，即使第二次检测成功，区域也很大。为此加入角度条件。

取名义源距离为后验均值
\[
G_*=\mu_r e(\bar\theta_1),\qquad \mu_r\approx855.26\,\mathrm m.
\]
要求
\[
\gamma=\angle(S_1,G_*,S_2)\in[30^\circ,150^\circ].
\]
在局部坐标下，\(S_2=(a,b)\)，\(G_*=(\mu_r,0)\)，有
\[
\cos\gamma
=
\frac{\mu_r-a}{\sqrt{(\mu_r-a)^2+b^2}}
\]
或等价地
\[
\boxed{
|a-\mu_r|\le\sqrt3\,|b|.
}
\]
把这个条件与 \(\mathcal C_0\) 相交，得到两个侧瓣：
\[
\boxed{
\mathcal C_{\rm cand}
=
\mathcal C_0
\cap
\{(a,b):|a-\mu_r|\le\sqrt3|b|\}.
}
\]
在世界坐标中，候选区域就是
\[
\left\{
S_1+a\,e(\bar\theta_1)+b\,e_\perp(\bar\theta_1):
(a,b)\in\mathcal C_{\rm cand}
\right\}.
\]
上下两个侧瓣几何对称，实际取哪一侧取决于地形或后续搜索便利性。

---

## 6. 小角度近似：快速理解权衡

因为 \(\varepsilon=1^\circ\) 很小，令
\[
t=\tan\varepsilon.
\]
两个楔形在源点附近可近似为两条宽度分别为
\[
w_1=2d_1t,\qquad w_2=2d_2t
\]
的条带。它们的夹角为
\[
\gamma=\angle(S_1,G,S_2).
\]
近似交会区域是一个平行四边形，其面积为
\[
\boxed{
A_{\rm lin}
=
\frac{w_1w_2}{\sin\gamma}
=
\frac{4d_1^2d_2^2t^2}{|\operatorname{cross}(G-S_1,G-S_2)|}.
}
\]
两条边长分别为
\[
L_1=\frac{2d_1d_2^2t}{|\operatorname{cross}(G-S_1,G-S_2)|},
\qquad
L_2=\frac{2d_1^2d_2t}{|\operatorname{cross}(G-S_1,G-S_2)|}.
\]
平行四边形的直径是较长对角线：
\[
D_{\rm lin}
=
\sqrt{L_1^2+L_2^2+2L_1L_2\cos\gamma},
\qquad
R_{\min}\approx\frac{D_{\rm lin}}2.
\]
这解释了候选点的选择：

- \(\sin\gamma\) 不能太小，否则 \(A_{\rm lin}\) 发散；
- \(d_2\) 也不能太大，否则 \(w_2\) 和交会面积增大；
- 因而 \(a\) 应在后验均值 \(\mu_r\approx855\) 附近，同时 \(b\) 取足够大以避开共线；
- 但 \(b\) 太大又会远离源、增大 \(d_2\)。

精确数值中 \(D(S_1,1500)\) 的截断效应不可忽略，所以最终数值使用第 4 节的精确多边形积分。

---

## 7. 至少需要多少个半径 \(20\,\mathrm m\) 的圆

设区域 \(\mathcal R\) 的面积为 \(A\)、直径为 \(D\)、最小包围圆半径为 \(R_{\min}\)，清除半径为
\[
r_c=20\,\mathrm m.
\]
我们使用如下**严格下界**：
\[
\boxed{
N_{20}^{\rm lb}
=
\begin{cases}
1,&R_{\min}\le r_c,\\[1mm]
\max\left(
2,\;
\left\lceil \dfrac{A}{\pi r_c^2}\right\rceil,\;
\left\lceil \dfrac{D}{2r_c}\right\rceil
\right),&R_{\min}>r_c.
\end{cases}
}
\]
理由：

1. 一个半径 \(r_c\) 的圆盘能覆盖 \(\mathcal R\) 当且仅当 \(R_{\min}\le r_c\)；
2. 若用 \(n\) 个半径 \(r_c\) 的圆盘覆盖，则总面积至少为 \(A\)，所以 \(n\ge A/(\pi r_c^2)\)；
3. 覆盖连通区域的圆盘并集直径不超过 \(2r_cn\)，所以 \(n\ge D/(2r_c)\)。

本文只给出这个下界，不求解精确最小覆盖数。精确地用给定半径的圆盘覆盖任意凸多边形，是一个连续覆盖优化问题；对本题 Q2 的策略探索来说，下界已能反映“区域是长条还是接近圆”的差异。若需要精确覆盖数，更适合放到 Q3 的清除动作序列中通过实际 `/clear` 来验证。

---

## 8. 数值结果与策略权衡

### 8.1 求积设置

使用 Gauss-Legendre 求积：

- \(r\) 在 \([0,1000]\) 和 \([1000,1500]\) 上分别取节点，避免 \(K_1\) 的折点；
- \(\phi\in[-1^\circ,1^\circ]\)；
- \(\delta_2\in[-1^\circ,1^\circ]\)；
- 每个节点处用半平面求交构造 \(\mathcal R\)，再用旋转卡壳求直径、用最小包围圆算法求 \(R_{\min}\)。

对表中所列点取约 \(32\times32\times24\times14\) 个节点；指示函数 \(P(R_{\min}\le20)\) 的收敛较慢，其最后一位小数只作参考。

### 8.2 候选点比较

| 第二点 \((a,b)\) | \(p_{\det}\) | \(\mathbb E[A]\) | \(\mathbb E[R_{\min}]\) | \(\mathrm{sd}(R_{\min})\) | \(P(R_{\min}\le20)\) | \(\mathbb E[N_{20}^{\rm lb}]\) |
|---|---:|---:|---:|---:|---:|---:|
| \((700,450)\) | 1.000000 | 812.19 | 28.474 | 14.557 | 41.03% | 1.847 |
| \((850,520)\) | 1.000000 | 720.99 | 25.361 | 8.574 | 35.59% | 1.731 |
| \((860,510)\) | 1.000000 | 709.27 | 25.335 | 8.445 | 34.47% | 1.739 |
| \((900,550)\) | 0.999901 | 724.08 | 25.095 | 7.079 | 28.92% | 1.767 |

从表中可读出三类策略：

1. **最大化一次 \(20\,\mathrm m\) 区域覆盖概率**
   \[
   (a,b)\approx(700,450).
   \]
   \(P(R_{\min}\le20)\approx41\%\)，但 \(\mathbb E[R_{\min}]\approx28.5\,\mathrm m\)，方差也最大。

2. **综合稳健**
   \[
   (a,b)\approx(850,520)\quad\text{或}\quad(860,510).
   \]
   第二次检测概率为 \(1\)，\(\mathbb E[R_{\min}]\approx25.3\sim25.4\,\mathrm m\)，标准差约 \(8.5\,\mathrm m\)，一次清除概率约 \(35\%\)。

3. **最小化期望定位误差，容忍极小检测风险**
   \[
   (a,b)\approx(900,550).
   \]
   \(p_{\det}\approx0.9999\)，\(\mathbb E[R_{\min}]\approx25.1\,\mathrm m\)，标准差约 \(7.1\,\mathrm m\)，但一次 \(20\,\mathrm m\) 清除概率降到约 \(29\%\)。

### 8.3 推荐

若没有特殊偏好，推荐第二点取
\[
\boxed{
S_2^\star
=
S_1+850\,e(\bar\theta_1)\pm520\,e_\perp(\bar\theta_1)
}
\]
或相邻的
\[
S_2^\star
=
S_1+860\,e(\bar\theta_1)\pm510\,e_\perp(\bar\theta_1).
\]
它们在 \(\mathcal C_0\) 内，因此第二次检测概率为 \(1\)，同时把期望最小包围圆半径和方差都控制得较好。

若后续 Q3 的策略更看重“一次 `/clear` 命中概率”，可以把候选区域向 \((700,450)\) 一侧移动；若更看重平均定位精度，则向 \((900,550)\) 一侧移动。\(\mathcal C_{\rm cand}\) 正好给出了这种权衡的搜索空间。

---

## 9. 算法流程

```text
输入：第一次成功检测点 S1、示向度 theta1

1. 若 S1 可规划，取 S1 = 目标区域中心 O。
2. 以 e(theta1) 为局部 x 轴，e_perp(theta1) 为局部 y 轴。
3. 构造保证检测区域 C0：
   C0 = D(S1,1000) ∩ D(S1+1500 e(theta1+1°),1000)
                      ∩ D(S1+1500 e(theta1-1°),1000)。
4. 计算后验均值 mu_r ≈ 855.26 m。
5. 取角度条件 |x - mu_r| <= sqrt(3)|y|。
6. 得到候选区域 C_cand = C0 ∩ {角度条件}，包含上下两个侧瓣。
7. 在 C_cand 中选择第二点：
      - 默认 (x,y) ≈ (850,520) 或 (860,510)；
      - 若优先一次清除，选约 (700,450)；
      - 若优先期望误差，选约 (900,550)（允许约 0.01% 检测失败风险）。
8. 按算得的 S2 进行第二次 /measure。
```

---

## 10. 实现与复现

运行配套脚本：

```bash
./cpp/build.sh

PYTHONPATH=cpp python3 script/q2_second_point_quadrature.py \
    --points "700,450 850,520 860,510 900,550" \
    --nr-low 32 --nr-high 32 --nphi 24 --ndelta 14 \
    --bound-sides 384
```

脚本输出 Markdown 表格，包含：

- \(p_{\det}\)：第二次检测成功概率；
- \(\mathbb E[A]\)：定位区域面积期望；
- \(\mathbb E[R_{\min}]\)：最小包围圆半径期望；
- \(\mathrm{sd}(R_{\min})\)：最小包围圆半径标准差；
- \(P(R_{\min}\le20)\)：一个 \(20\,\mathrm m\) 清除圆即可覆盖的概率；
- \(\mathbb E[N_{20}^{\rm lb}]\)：覆盖数下界的期望。

### 10.1 交互式热图

另配套一个 Flask + Plotly 的交互热图程序：

```bash
PYTHONPATH=cpp python3 script/q2_dashboard.py --port 8055
```

浏览器打开 `http://127.0.0.1:8055`。程序支持：

- 拖动改变第一观测点 \(S_1=(S_1.x,S_1.y)\)；
- 拖动改变第一示向度 \(\theta_1\)。这里 \(\theta_1\) 是 \(x\) 轴正向逆时针旋转到 \(S_1\) 指向干扰源方向的示向角；
- 选择随机变量为 \(R_{\min}\) 或 \(N_{20}^{\rm lb}\)；
- 选择统计量为期望或方差；
- 热图坐标 \((u,v)\) 是第二点的**绝对场地坐标**：
  \[
  u=S_2.x,\qquad v=S_2.y,
  \]
  原点为半径 \(1800\text{ m}\) 的场地圆心。因此目标圆盘就是
  \[
  \Omega=\{(u,v):u^2+v^2\le1800^2\}.
  \]

热图后端仍使用本节相同的确定性求积和 C++ 几何内核，不是蒙特卡洛。白色虚线是 \(p_{\det}=0.999\) 等值线，用于区分“几乎保证第二次检测”的区域。

界面提供以下可视化选项，默认尽量把目标区域内的有限值都显示出来：

1. 颜色映射可选
   \[
   \text{linear},\qquad \log_{10}(1+z),\qquad \sqrt z,\qquad \text{分位/秩着色}.
   \]
   默认使用 \(\log_{10}(1+z)\)。这样 \(R_{\min}\) 从 \(25\text{ m}\) 到几百米、方差跨越数个数量级的格点都能显示，而不是只用少数极端值定色标；
2. 可选的“近平行交会区”掩膜。该掩膜在第二点的局部坐标中定义：
   \[
   u_{\rm loc}=(S_2-S_1)\cdot e(\theta_1),\qquad
   v_{\rm loc}=(S_2-S_1)\cdot e_\perp(\theta_1),
   \]
   \[
   |u_{\rm loc}-855.26|>\sqrt3\,|v_{\rm loc}|.
   \]
   这一带内两条示向线夹角趋近 \(0^\circ\) 或 \(180^\circ\)，条件数发散，\(R_{\min}\) 和 \(N_{20}^{\rm lb}\) 会急剧增大，但不是可用的第二点选择；
3. 可选的 \(p_{\det}\) 掩膜，默认阈值为 \(0.99\)；
4. 线性模式下可选按有效格点的 \(2\%\sim98\%\) 分位裁剪；
5. 可叠加场地圆 \(u^2+v^2=1800^2\)，并可用“只显示第二点在场地圆内的格点”把圆外格点置灰；
6. 按钮“按 1800m 场地圆定范围”会把热图范围设为
   \[
   u\in[-1800,1800],\qquad v\in[-1800,1800].
   \]

被掩膜的区域会用灰色背景显示，而不是直接消失。若不勾选掩膜，配合对数色标可以查看所有有限格点，用白色 \(p_{\det}=0.999\) 等值线区分可靠区域。

默认推荐候选点局部坐标为 \((850,\pm520)\)，转换到绝对场地坐标后为
\[
S_2^\star
=
S_1+850\,e(\theta_1)\pm520\,e_\perp(\theta_1),
\]
热图中的两个红色星号就是按此式转换后画出的。

---

## 11. 局限与后续

1. **源点先验**  
   本文采用 \(\Omega\) 内等面积均匀。若实际有地形、道路或历史数据导致非均匀先验，应把这些先验写进第 4 节的积分；第一点最优位置也相应改变。

2. **有效半径先验**  
   本文取 \(\rho\sim\mathrm{Uniform}[1000,1500]\)。若只知道 \(\rho\in[1000,1500]\) 而不知道分布，推荐使用 \(\mathcal C_0\) 作为稳健候选区域；它不依赖 \(\rho\) 的具体分布。若只知道上界 \(1500\)，则检测约束会退化为 \(d_2\le1500\)，可把候选区域扩大，但定位质量也会下降。

3. **精确覆盖数**  
   本文只给了半径 \(20\,\mathrm m\) 圆覆盖数的严格下界，没有求解精确最小值。精确覆盖数更适合在 Q3 的清除动作中处理。

4. **第一点已给定且不在中心**  
   此时把第 3 节的径向积分上限换成沿各方向的 \(L(\phi)\)，并在构造 \(\mathcal C_0\) 时用 \(\mathcal A_1\) 的极值点（可能包含目标圆盘边界上的切割点）即可。数值步骤不变。

5. **指示函数求积**  
   \(P(R_{\min}\le20)\) 的被积函数是阶跃函数，Gauss 求积的尾部误差比 \(\mathbb E[R_{\min}]\) 更明显。若该概率要作为最终硬指标，建议后续加入区域边界自适应细分，或用更细的确定性网格验证。

---

## 12. 结论

1. 若第一点可以规划，选中心 \(O\) 最稳；它使第一次成功检测概率最大，数值约为 \(48.87\%\)。
2. 第一次成功后的源距离后验均值为 \(\mu_r\approx855.26\,\mathrm m\)。
3. 第二点不应放在第一测向线上，否则两线近平行，定位区域被拉长。
4. 保证第二次检测的稳健区域是由三个半径 \(1000\,\mathrm m\) 圆盘相交得到的透镜形区域 \(\mathcal C_0\)。
5. 再加入角度条件 \(|x-\mu_r|\le\sqrt3|y|\)，得到上下两个候选侧瓣 \(\mathcal C_{\rm cand}\)。
6. 默认推荐 \(S_2=S_1+850e(\bar\theta_1)\pm520e_\perp(\bar\theta_1)\)；若偏向一次清除，向 \((700,450)\) 移动；若偏向最小期望误差，向 \((900,550)\) 移动。
