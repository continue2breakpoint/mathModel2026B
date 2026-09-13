# 本轮核验记录

- 最终参数第三问消融：已重新运行三组种子1–30、1–60、61–160，结果在 `data/q3-final-ablation.json`。
- 公式复核：`python3 paper/verify_formulas.py` 通过，增加最终6/1130布局；覆盖半径996.949676米。
- 第四问连续单元证书：调用现有验证器重验24点布局，3107检查、1992直接通过、未决0；见 `data/layout-certificate-recheck.json`。
- 现有几何、覆盖清除与版本策略测试通过：`python3 -m pytest framework/tests/test_v18_region_clear.py framework/tests/test_geometry.py framework/tests/test_versioned_strategies.py -q`。初次沙箱禁止本地套接字；获准后在沙箱外重跑成功。
- 第四问18份历史复核JSON汇总确认：v18共520案例、6684源全部清除；没有重新宣称执行该批历史实验。
- 6张SVG和对应PDF已生成；PDF中文嵌入改用兼容字体输出，并使用MuPDF渲染检查摘要、流程、Q2候选区、布局及实验页。
- 核对当前源码快照与原文件哈希一致，正文交叉引用齐全。
- 未改变最终算法源码，未调用官方演练或正式接口。

人工待补项目见 `FINAL_CHECKLIST.md`。正式测试表的留空是缺少真实成绩，并非用演练替代。
