// 登录页 —— passthrough 期极简版 (真 auth 波接入 /api/v1/auth/login 后替换)。
// 现阶段: 填用户名即写 localStorage + 进控制台 (后端 passthrough 信任 X-User-Id, 不验签)。
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { history, useModel } from '@umijs/max';
import { Button, Card, Form, Input, message } from 'antd';

export default function LoginPage() {
  const { refresh } = useModel('@@initialState');

  const onFinish = async (values: { username: string }) => {
    // TODO(auth 波): 调 POST /api/v1/auth/login 换 token; 现在 passthrough 直接写本地态。
    localStorage.setItem('auth_token', `dev-${values.username}`);
    localStorage.setItem('user', values.username);
    await refresh();
    message.success('已登录');
    history.push('/projects');
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
      <Card title="codev-platform 控制台登录" style={{ width: 360 }}>
        <Form onFinish={onFinish} initialValues={{ username: 'local' }}>
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
