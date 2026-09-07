"""web.security —— 密码哈希 / 会话 / 登录态依赖 (plan §十五 Auth)。

密码 pbkdf2 不明文 (security.md); 会话与 gateway 静态 token 解耦 (登录态走本模块 SessionStore)。
"""
