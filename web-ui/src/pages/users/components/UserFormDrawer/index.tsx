import Drawer from '@/components/Drawer';
import { Form, Input, message, Select } from 'antd';
import { useCallback, useEffect } from 'react';

import { createUser, updateUser, type UserRow } from '../utils';

const filterOption = (input: string, option?: { label?: string }) =>
  String(option?.label ?? '')
    .toLowerCase()
    .includes(input.toLowerCase());

export interface UserFormDrawerContext {
  open: boolean;
  mode: 'create' | 'edit';
  record?: UserRow;
}

interface UserFormDrawerProps {
  context: UserFormDrawerContext;
  orgOptions: { label: string; value: string }[];
  roleOptions: { label: string; value: string }[];
  onOk: () => void;
  onCancel: () => void;
}

const UserFormDrawer: React.FC<UserFormDrawerProps> = ({
  context,
  orgOptions,
  roleOptions,
  onOk,
  onCancel,
}) => {
  const { open, mode, record } = context;
  const [form] = Form.useForm();

  useEffect(() => {
    if (!open) {
      return;
    }
    if (mode === 'edit' && record) {
      form.setFieldsValue({
        username: record.username,
        displayName: record.displayName,
        email: record.email,
      });
    } else {
      form.resetFields();
    }
  }, [open, mode, record, form]);

  const handleOk = useCallback(async (): Promise<void> => {
    const values = await form.validateFields();
    if (mode === 'create') {
      await createUser(values as API.UserCreateRequest);
      message.success('用户创建成功');
    } else {
      await updateUser({
        username: record?.username ?? '',
        displayName: values.displayName,
        email: values.email,
      });
      message.success('用户信息已更新');
    }
    onOk();
  }, [form, mode, record, onOk]);

  const title = mode === 'create' ? '新增用户' : '编辑用户';

  return (
    <Drawer
      title={title}
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      size="large"
      destroyOnHidden
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="username"
          label="用户名"
          rules={[{ required: true, message: '请输入用户名' }]}
        >
          <Input placeholder="登录用户名 (唯一键)" maxLength={64} disabled={mode === 'edit'} />
        </Form.Item>
        {mode === 'create' && (
          <>
            <Form.Item
              name="password"
              label="初始密码"
              rules={[{ required: true, message: '请输入初始密码' }]}
            >
              <Input.Password placeholder="初始密码 (明文仅入参)" maxLength={128} />
            </Form.Item>
            <Form.Item
              name="orgId"
              label="归属组织"
              rules={[{ required: true, message: '请选择归属组织' }]}
            >
              <Select
                options={orgOptions}
                placeholder="选择归属组织"
                showSearch={{ filterOption }}
              />
            </Form.Item>
            <Form.Item name="role" label="组织角色">
              <Select options={roleOptions} placeholder="选择组织角色 (可留空)" allowClear />
            </Form.Item>
          </>
        )}
        <Form.Item name="displayName" label="显示名">
          <Input placeholder="显示名称" maxLength={200} />
        </Form.Item>
        <Form.Item name="email" label="邮箱">
          <Input placeholder="邮箱地址" maxLength={200} />
        </Form.Item>
      </Form>
    </Drawer>
  );
};

export default UserFormDrawer;
