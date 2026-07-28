# Tests

跨模块测试入口。插件内单元测试仍放在 `apps/obsidian-plugin/tests`。

```powershell
npm run test:plugin
npm run test:python
npm run verify
```

`npm run package:windows` 会先执行 `verify`，只有全部测试通过才生成 Windows 一体包。
