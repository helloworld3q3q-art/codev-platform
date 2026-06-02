// 登录页 —— 真鉴权: POST /api/v1/auth/login 换 token (后端 Auth 波 + 种子 admin 已就绪)。
// 失败由 fetch 统一弹错; 成功存 access/refresh token + 用户名, 刷新 initialState 后进控制台。
import { useCallback } from 'react';
import { history, useModel } from '@umijs/max';

import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Button, Card, Form, Input, message } from 'antd';

import { postLogin } from '@/services/apis/authapi';

interface LoginValues {
  username: string;
  password: string;
}

export default function LoginPage() {
  const { refresh } = useModel('@@initialState');

  const handleFinish = useCallback(
    async (values: LoginValues): Promise<void> => {
      try {
        const res = await postLogin({ username: values.username, password: values.password });
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
        history.replace('/projects');
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
