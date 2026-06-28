# MtkDirectTool

`MtkDirectTool.jar` is bundled at `assets/runtime/MtkDirectTool.jar`.
MonitorToolbox deploys it to the display and runs it through `TvService`.

It provides:

- generic MTK config `get`, `set`, `getMinMax`, and `dump`
- `batchGet <key...>` to read multiple MTK config keys in one TvService call
- `setColorGains <red> <green> <blue>` for custom color temperature gain
- `setHdrToneMapping <mtkValue> [isUpdate]` for HDR tone mapping; this mirrors
  Xiaomi's native path by also calling `IMiTVProvider.setHdrAttr(4, value)`

The color gain command uses the factory white-balance API
`MtkTvFApiDisplay.setWbGainOffsetEx(...)` so red, green, and blue are committed
as one group. Writing the three `g_video__clr_gain_*` keys independently can
make the firmware restore untouched channels to `1024`.

Rebuild the bundled jar from `MtkDirectTool.java` after changing this source.
