/** web-ui 开发工具连接后端时使用的唯一地址真值源。 */
export const DEFAULT_BACKEND_ORIGIN = 'http://127.0.0.1:18088';

type Environment = Readonly<Record<string, string | undefined>>;

/**
 * 解析后端 origin。默认只访问本机；确需跨主机时通过环境变量显式覆盖。
 */
export function resolveBackendOrigin(env: Environment = process.env): string {
  const configured = env.CODEV_WEB_BACKEND_ORIGIN?.trim();
  const candidate = configured || DEFAULT_BACKEND_ORIGIN;

  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    throw new Error('CODEV_WEB_BACKEND_ORIGIN 必须是有效的 HTTP(S) origin');
  }

  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('CODEV_WEB_BACKEND_ORIGIN 只允许 HTTP(S) 协议');
  }
  if (url.username || url.password) {
    throw new Error('CODEV_WEB_BACKEND_ORIGIN 不允许包含凭据');
  }
  if (url.pathname !== '/' || url.search || url.hash) {
    throw new Error('CODEV_WEB_BACKEND_ORIGIN 只能包含协议、主机和端口');
  }
  return url.origin;
}

/** 使用统一 origin 拼接受控后端路径。 */
export function backendUrl(pathname: string, env: Environment = process.env): string {
  if (!pathname.startsWith('/') || pathname.startsWith('//')) {
    throw new Error('后端路径必须是以单个 / 开头的绝对路径');
  }
  return `${resolveBackendOrigin(env)}${pathname}`;
}
