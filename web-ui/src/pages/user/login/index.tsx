// 登录页 —— passthrough 期版本 (真 auth 波: 改调 /api/v1/auth/login 换 token)。
// 现阶段后端 gateway passthrough 信任 X-User-Id, 前端填用户名即写本地登录态进控制台。
import { useCallback } from 'react';
import { history, useModel } from '@umijs/max';

import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Button, Card, Form, Input, message } from 'antd';

import type { UserInfo } from '@/models/user';

export default function LoginPage() {
  const { refresh } = useModel('@@initialState');

  const handleFinish = useCallback(
    async (values: { username: string }): Promise<void> => {
      const userInfo: UserInfo = { username: values.username };
      localStorage.setItem('auth_token', `dev-${values.username}`);
      localStorage.setItem('user', JSON.stringify(userInfo));
      await refresh();
      message.success('已登录');
      history.replace('/projects');
    },
    [refresh],
  );

  return (
    <div className="flex justify-center pt-120">
      <Card title="codev-platform 控制台登录" classNames={{ root: 'w-380' }}>
        <Form onFinish={handleFinish} initialValues={{ username: 'local' }}>
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名 (passthrough)" />
          </Form.Item>
          <Form.Item name="password">
            <Input.Password prefix={<LockOutlined />} placeholder="密码 (auth 波启用)" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
