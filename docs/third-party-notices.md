# 第三方组件

本项目自身保持 MIT。第三方组件保留其原许可与版权声明，不将其重新声明为项目自身许可。

- SQLite：公共领域，通过 Python sqlite3 使用。https://www.sqlite.org/copyright.html
- lxml：BSD-3-Clause；通过 requirements-lock.txt 安装，保留安装包自带的许可证及其底层依赖声明。https://github.com/lxml/lxml/blob/master/LICENSE.txt
- charset-normalizer：MIT；通过 requirements-lock.txt 安装，保留其版权声明。https://github.com/jawah/charset_normalizer/blob/master/LICENSE

本版不捆绑 Calibre 二进制，只保留已有外部调用；不引入 EbookLib。
测试节选来自维基文库对应作品页面，来源 URL 和内容 hash 在 tests/fixtures/small/manifest.json 中。
原作品为公开的古典/鲁迅文本，节选用于解析回归，不作为模型微调数据。
