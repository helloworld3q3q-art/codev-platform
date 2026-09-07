// 登录页 —— 真鉴权 + 口令加密 (方案 B): 拉 RSA 公钥 → JSEncrypt 加密口令再传, 请求体不出现明文。
// 拉公钥/加密失败则降级明文 (后端兼容); 传输层加密靠 TLS (方案 A, 部署层)。
import { useCallback } from 'react';
import { history, useModel } from '@umijs/max';

import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Button, Card, Form, Input, message } from 'antd';
import { JSEncrypt } from 'jsencrypt';

import { postLogin } from '@/services/apis/authapi';
import { get } from '@/utils/fetch';
import type { BaseApiResponse } from '@/utils/fetch';

interface LoginValues {
  username: string;
  password: string;
}

// 拉公钥并 RSA 加密口令; 任一步失败 → 返回明文 (后端 _maybe_decrypt 兼容明文)。
async function encryptPassword(plain: string): Promise<string> {
  try {
    const res = await get<BaseApiResponse<{ publicKey?: string }>>({
      url: '/api/v1/auth/public-key',
    });
    const pub = res.data?.publicKey;
    if (!pub) {
      return plain;
    }
    const encryptor = new JSEncrypt();
    encryptor.setPublicKey(pub);
    const encrypted = encryptor.encrypt(plain);
    return encrypted || plain;
  } catch {
    return plain;
  }
}

export default function LoginPage() {
  const { refresh } = useModel('@@initialState');

  const handleFinish = useCallback(
    async (values: LoginValues): Promise<void> => {
      try {
        const password = await encryptPassword(values.password);
        const res = await postLogin({ username: values.username, password });
        const pair = res.data;
        if (!pair?.accessToken) {
          return;
        }
        localStorage.setItem('auth_token', pair.accessToken);
        if (pair.refreshToken) {
          localStorage.setItem('refresh_token', pair.refreshToken);
        }
        localStorage.setItem('user', JSON.stringify({ username: values.username }));
        await refresh();
        message.success('登录成功');
        history.replace('/dashboard');
      } catch {
        // 凭据错误等已由 fetch 统一处理弹错。
      }
    },
    [refresh],
  );

  return (
    <div className="flex justify-center pt-120">
      <Card title="codev-platform 控制台登录" classNames={{ root: 'w-380' }}>
        <Form<LoginValues> onFinish={handleFinish} initialValues={{ username: 'root' }}>
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
