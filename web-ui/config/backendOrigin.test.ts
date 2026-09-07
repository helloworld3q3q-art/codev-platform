import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_BACKEND_ORIGIN,
  backendUrl,
  resolveBackendOrigin,
} from './backendOrigin';

test('未配置时只访问本机 WSL 后端', () => {
  assert.equal(DEFAULT_BACKEND_ORIGIN, 'http://127.0.0.1:18088');
  assert.equal(resolveBackendOrigin({}), DEFAULT_BACKEND_ORIGIN);
});

test('显式地址统一规范为无尾斜杠的 origin', () => {
  assert.equal(
    resolveBackendOrigin({ CODEV_WEB_BACKEND_ORIGIN: '  http://192.0.2.10:18088/  ' }),
    'http://192.0.2.10:18088',
  );
});

test('后端地址拒绝凭据、路径和非 HTTP 协议', () => {
  assert.throws(() =>
    resolveBackendOrigin({ CODEV_WEB_BACKEND_ORIGIN: 'http://user:secret@127.0.0.1:18088' }),
  );
  assert.throws(() =>
    resolveBackendOrigin({ CODEV_WEB_BACKEND_ORIGIN: 'http://127.0.0.1:18088/api' }),
  );
  assert.throws(() =>
    resolveBackendOrigin({ CODEV_WEB_BACKEND_ORIGIN: 'file:///tmp/codev' }),
  );
});

test('子路径由同一 origin 安全拼接', () => {
  assert.equal(backendUrl('/openapi.json', {}), 'http://127.0.0.1:18088/openapi.json');
});
