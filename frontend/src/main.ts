import { createApp } from 'vue'
import { createPinia } from 'pinia'
import 'element-plus/es/components/base/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/breadcrumb/style/css'
import 'element-plus/es/components/breadcrumb-item/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/cascader/style/css'
import 'element-plus/es/components/checkbox/style/css'
import 'element-plus/es/components/checkbox-group/style/css'
import 'element-plus/es/components/config-provider/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/dropdown/style/css'
import 'element-plus/es/components/dropdown-item/style/css'
import 'element-plus/es/components/dropdown-menu/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/menu/style/css'
import 'element-plus/es/components/menu-item/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/popover/style/css'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/tree/style/css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import router from './router'
import './styles/app.css'
import './styles/element.css'

// Element Plus 组件不再在入口全量注册：每个用到 <el-*> 的 SFC 自己 import，
// 由 Rollup 按路由 chunk 拆分。全量注册会把 29 个组件（约 320KB JS）压进首屏
// 主包，首屏多解析/编译一大截，实际大部分组件只在设置、组织树等懒加载页用到。
// 组件样式仍统一在入口引入，避免每个 SFC 重复写 style/css。
const app = createApp(App)
app.use(createPinia())
app.use(router)

app.mount('#app')
