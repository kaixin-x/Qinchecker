# 配置说明

- `admin_aliases.csv`：历史行政区名到现行标准名的映射。标准化后在主表的备注列追加变更说明。
- `qinling_counties.csv`：秦岭核心县与边缘县清单。`scope` 取值为 `core` 或 `boundary`；`status=verified` 才能自动写入主表，`candidate` 仅显示为待复核，`excluded` 明确排除。这样可以先逐步补齐边缘县，而不会把尚未核验的地名误写入结果。
- `habitat_terms.csv`：可安全自动翻译的生境术语。未命中或存在歧义的文本必须进入待复核。
- `field_rules.json`：本项目已确认的字段更新策略。
- `settings.json`：批处理、预览、导出和来源留存策略。
