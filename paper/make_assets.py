"""Generate publication figures and tables from final source and recorded experiments."""
from pathlib import Path
import os, sys, json, math, shutil, hashlib, ast
os.environ.setdefault('MPLCONFIGDIR','/tmp/mathmodel-paper-mpl')
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Rectangle, FancyBboxPatch
ROOT=Path(__file__).resolve().parents[1]; PAPER=ROOT/'paper'
sys.path.insert(0,str(ROOT/'framework/src'))
from mathmodel2026b.strategy_v17 import LAYOUT_Q4_V17
from mathmodel2026b.geometry import Point
from mathmodel2026b.knowledge_layer import cells_covering_region
plt.rcParams.update({'font.family':'Noto Sans CJK JP','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'none','pdf.fonttype':3,'axes.unicode_minus':False})
# Select installed CJK family, keeping SVG labels editable.
from matplotlib.font_manager import fontManager
families={f.name for f in fontManager.ttflist}
for family in ['Noto Sans CJK SC','Noto Sans CJK JP']:
 if family in families: plt.rcParams['font.family']=family; break
for folder in ['figures','tables','data']: (PAPER/folder).mkdir(exist_ok=True)
B='#23658c'; G='#17806b'; R='#be5147'; GOLD='#cd9937'
def save(fig,name):
 fig.savefig(PAPER/'figures'/f'{name}.svg',bbox_inches='tight');fig.savefig(PAPER/'figures'/f'{name}.pdf',bbox_inches='tight');plt.close(fig)
def geom(ax,xlim,ylim):
 ax.set_aspect('equal');ax.set_xlim(*xlim);ax.set_ylim(*ylim);ax.set_xlabel('x / m');ax.set_ylabel('y / m');ax.grid(alpha=.15)
# Logical workflow.
fig,ax=plt.subplots(figsize=(10,4.6));ax.set(xlim=(0,10),ylim=(0,4.6));ax.axis('off')
boxes=[(0.1,3.25,'发现布局\nQ3 七点 / Q4 24点'),(3.65,3.25,'联合任务调度\n扫描点 + 待清目标'),(7.2,3.25,'执行检测与试清\n记录实际反馈'),(7.2,.7,'知识矩阵与可行域\n正观测 + 失败排除圆'),(3.65,.7,'补测或覆盖清除\n25米相交格 / 成本筛选'),(.1,.7,'完成核验\n发现覆盖 + 成功清除')]
for x,y,t in boxes:
 ax.add_patch(FancyBboxPatch((x,y),2.65,1,boxstyle='round,pad=0.09',fc='#edf4f6',ec=B,lw=1.3));ax.text(x+1.325,y+.5,t,ha='center',va='center')
for a,b in [((2.85,3.75),(3.5,3.75)),((6.4,3.75),(7.05,3.75)),((8.5,3.12),(8.5,1.85)),((7.05,1.2),(6.4,1.2)),((3.5,1.2),(2.85,1.2)),((5,1.85),(5,3.1))]: ax.annotate('',xy=b,xytext=a,arrowprops={'arrowstyle':'->','color':B,'lw':1.6})
ax.text(5.1,2.48,'更新后重规划',va='center',fontsize=9);save(fig,'workflow')
# Q1 triangle vs parallelogram.
fig,axs=plt.subplots(1,2,figsize=(9,3.5))
for ax in axs: ax.set_aspect('equal'); ax.axis('off'); ax.set(xlim=(-1.8,1.8),ylim=(-1.4,2.05))
v=np.array([[-1,-.5],[.7,-.5],[1,.5],[-.7,.5]])
axs[0].add_patch(Polygon(v,fc='#e2f0ee',ec=G));axs[0].add_patch(Circle((0,0),math.sqrt(1.25),fill=False,ec=B));axs[0].plot([-1,1],[-.5,.5],'--',color=R);axs[0].set_title('平行四边形：直径圆可以覆盖')
v=np.array([[-1,0],[1,0],[0,math.sqrt(3)]])
axs[1].add_patch(Polygon(v,fc='#edf4f6',ec=B));axs[1].add_patch(Circle((0,0),1,fill=False,ec=R,ls='--'));axs[1].add_patch(Circle((0,1/math.sqrt(3)),2/math.sqrt(3),fill=False,ec=G));axs[1].scatter([0],[math.sqrt(3)],color=R,s=30);axs[1].text(1.05,-.45,'直径圆',color=R);axs[1].text(.95,1.4,'最小包围圆',color=G);axs[1].set_title('等边三角形：直径圆不能覆盖');save(fig,'diameter')
# Q2 robust candidate region from the exact formulas in the paper.
fig,ax=plt.subplots(figsize=(8,5.1));x=np.linspace(-100,1550,661); y=np.linspace(-1100,1100,681);X,Y=np.meshgrid(x,y);eps=math.pi/180;mu=855.277
centers=[(0,0),(1000*math.cos(eps),-1000*math.sin(eps)),(1000*math.cos(eps),1000*math.sin(eps))]
robust=np.logical_and.reduce([(X-cx)**2+(Y-cy)**2<=1e6 for cx,cy in centers]); cand=robust & (abs(X-mu)<=math.sqrt(3)*abs(Y))
ax.contourf(X,Y,np.where(cand,2,np.where(robust,1,0)),levels=[.5,1.5,2.5],colors=['#dce7ed','#79b6a4'],alpha=.95)
for k,(cx,cy) in enumerate(centers): ax.add_patch(Circle((cx,cy),1000,fill=False,ec=B,lw=.7,alpha=.6))
a=np.linspace(-eps,eps,80);verts=np.vstack([[0,0],np.c_[1500*np.cos(a),1500*np.sin(a)],[0,0]]);ax.add_patch(Polygon(verts,fc=GOLD,ec=GOLD,alpha=.8))
ax.scatter([0,850,850],[0,520,-520],c=[B,R,R],s=28,zorder=5)
ax.annotate(r'$S_1$',(0,0),xytext=(-60,70));ax.annotate('(850, 520)',(850,520),xytext=(910,610),color=R);ax.annotate('(850, −520)',(850,-520),xytext=(910,-690),color=R)
ax.text(400,700,'角度筛选候选区',ha='center',color=G);ax.text(1180,100,'首次示向扇形',color=GOLD);geom(ax,(-100,1550),(-1100,1100));ax.set_xlabel('沿第一示向方向 / m');ax.set_ylabel('垂直第一示向方向 / m');save(fig,'q2_candidates')
# Final discovery layouts (not an observed online trajectory).
fig,axs=plt.subplots(1,2,figsize=(10,4.6));a=np.arange(6)*math.pi/3
q3=np.vstack([[0,0],np.c_[1130*np.cos(a),1130*np.sin(a)]]);q4=np.array(LAYOUT_Q4_V17)
for ax,pts,title in zip(axs,[q3,q4],['问题三：圆心 + 六边形','问题四：24点联合优化布局']):
 ax.add_patch(Circle((0,0),1800,fc='#f2f6f7',ec=B,lw=1.1));ax.scatter(pts[:,0],pts[:,1],color=G,s=24,zorder=4)
 for k,(xx,yy) in enumerate(pts): ax.annotate(str(k+1),(xx,yy),xytext=(4,4),textcoords='offset points',fontsize=7)
 if len(pts)==24:
  route=np.vstack([[0,0],pts]);ax.plot(route[:,0],route[:,1],color=G,alpha=.65,lw=.9)
 else:
  for xx,yy in pts: ax.add_patch(Circle((xx,yy),1000,fill=False,ec=G,alpha=.2,lw=.8))
 geom(ax,(-2250,2250),(-2250,2250));ax.set_title(title)
save(fig,'layouts')
# Plot exact intersecting grid cells using final implementation.
fig,ax=plt.subplots(figsize=(7,4.5));poly=[Point(0,0),Point(100,24),Point(102,40),Point(5,20)]
cells,_,_,_=cells_covering_region(poly);ax.add_patch(Polygon([(v.x,v.y) for v in poly],fc='#b7d5e4',ec=B,lw=1.5,zorder=2))
for cx,cy in cells:
 ax.add_patch(Rectangle((cx-12.5,cy-12.5),25,25,fill=False,ec='#89959d',lw=.7));ax.scatter(cx,cy,c=G,s=12,zorder=4)
q=(12.5,12.5);ax.add_patch(Circle(q,20,fc=R,ec=R,alpha=.15));ax.plot(*q,'x',c=R,ms=8)
ax.add_patch(Circle((62.5,37.5),20,fill=False,ec=G,lw=1.4));ax.annotate('25 m',(37.5,0),xytext=(39,-12));geom(ax,(-15,130),(-18,80));save(fig,'cover_clear')
# Freeze only input records actually used.
if (PAPER/'build/q3-final-ablation.json').exists():
 shutil.copy2(PAPER/'build/q3-final-ablation.json',PAPER/'data/q3-final-ablation.json')
shutil.copy2(ROOT/'docs/data/official-drill-20260913-1420-q18.json',PAPER/'data/official-drill-q18.json')
shutil.copy2(ROOT/'logs/q4/certificate.json',PAPER/'data/layout-certificate.json')
q3data=json.loads((PAPER/'data/q3-final-ablation.json').read_text())['seed_sets']
keys=list(q3data['1-30']); labels=['v8基线','v15旧布局，or-opt关','v15最终布局，or-opt关','v15最终布局，or-opt开','v18最终布局，or-opt关','v18最终版']
lines=[r'\begin{tabular}{lrrr}\toprule',r'方法&种子1--30&种子1--60&种子61--160\\\midrule']
for label,key in zip(labels,keys): lines.append(label+'&'+'&'.join(f'{q3data[s][key]["avg_median"]:.2f}' for s in ['1-30','1-60','61-160'])+r'\\')
lines += [r'\bottomrule\end{tabular}'];(PAPER/'tables/q3-ablation.tex').write_text('\n'.join(lines))
audits={}; lines=[r'\begin{tabular}{llrrrr}\toprule',r'种子段&场景&v14&v17&v18&v18秒/源\\\midrule']
for lo,hi in [(1,60),(61,160),(161,260)]:
 for mode,zh in [('mixed','混合'),('all_directional','全定向')]:
  row=[]
  for arm in ['q4-v14','q4-v17','q4-v18']:
   file=ROOT/f'logs/audit/{mode}_{arm}_{lo}_{hi}.json'; d=json.loads(file.read_text());summary=d['summary']; audits[file.name]={'source':str(file.relative_to(ROOT)),'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'summary':summary};row.append(f'{summary["full_clear_cases"]}/{summary["n_cases"]}')
  lines.append(f'{lo}--{hi}&{zh}&'+ '&'.join(row)+f'&{summary["avg_clear_time_median"]:.2f}'+r'\\')
lines += [r'合计&两场景&470/520&505/520&520/520&---\\',r'\bottomrule\end{tabular}'];(PAPER/'tables/q4-audit.tex').write_text('\n'.join(lines));(PAPER/'data/q4-audit-summary.json').write_text(json.dumps(audits,ensure_ascii=False,indent=2))
fig,axs=plt.subplots(1,2,figsize=(10,4.1)); chosen=[1,2,3,5];vals=[q3data['61-160'][keys[i]]['avg_median'] for i in chosen]
axs[0].bar(range(4),vals,color=[B,B,G,G]);axs[0].set_xticks(range(4),['旧布局','最终布局','+ or-opt','+ 覆盖清除']);axs[0].set_ylim(250,285);axs[0].set_ylabel('每源时间中位数 / s（纵轴从250起）')
for i,v in enumerate(vals):axs[0].text(i,v+.8,f'{v:.2f}',ha='center',fontsize=9)
for arm,col in [('q4-v14',B),('q4-v17',GOLD),('q4-v18',G)]:
 vals=[audits[f'{mode}_{arm}_{lo}_{hi}.json']['summary']['full_clear_rate']*100 for lo,hi in [(1,60),(61,160),(161,260)] for mode in ['mixed','all_directional']];axs[1].plot(range(6),vals,'o-',label=arm,c=col,ms=4)
axs[1].set_xticks(range(6),['1–60\n混合','1–60\n定向','61–160\n混合','61–160\n定向','161–260\n混合','161–260\n定向'],fontsize=8);axs[1].set_ylim(0,107);axs[1].set_ylabel('全清案例比例 / %');axs[1].legend(loc='lower left',fontsize=8);fig.tight_layout();save(fig,'ablation')
print('Generated 6 SVG/PDF figures and 2 data tables.')
print('Q3 cover radius:',max(1130/(2*math.cos(math.pi/6)),math.sqrt(1800**2+1130**2-2*1800*1130*math.cos(math.pi/6))))
