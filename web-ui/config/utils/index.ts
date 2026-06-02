import routesData from '../routes';

type RouteItem = { path?: string; routes?: RouteItem[]; redirect?: string };

const collectPaths = (items: RouteItem[]): string[] => {
  const paths: string[] = [];
  for (const r of items) {
    if (!r.redirect && r.path) {
      paths.push(r.path);
    }
    if (r.routes) {
      paths.push(...collectPaths(r.routes));
    }
  }
  return paths;
};

// 不需要缓存的路由前缀
const excludePrefixes: string[] = ['/welcome', '/user', '/examples', '/system/404'];

export const keepalivePaths = collectPaths(routesData).filter((p) => {
  if (excludePrefixes.some((prefix) => p.startsWith(prefix))) {
    return false;
  }
  // 只保留二级及以上路由（路径中至少有两段，如 /booking/bookingmanagement）
  const segments = p.split('/').filter(Boolean);
  if (segments.length < 2) {
    return false;
  }
  return true;
});
