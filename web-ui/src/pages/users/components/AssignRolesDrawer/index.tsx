import Drawer from '@/components/Drawer';
import { Form, message, Select } from 'antd';
import { useCallback, useEffect } from 'react';

import { setUserRole, type UserRow } from '../utils';

const filterOption = (input: string, option?: { label?: string }) =>
  String(option?.label ?? '')
    .toLowerCase()
    .includes(input.toLowerCase());

export interface AssignRolesDrawerContext {
  open: boolean;
  record?: UserRow;
}

interface AssignRolesDrawerProps {
  context: AssignRolesDrawerContext;
  orgOptions: { label: string; value: string }[];
  roleOptions: { label: string; value: string }[];
  onOk: () => void;
  onCancel: () => void;
}

const AssignRolesDrawer: React.FC<AssignRolesDrawerProps> = ({
  context,
  orgOptions,
  roleOptions,
  onOk,
  onCancel,
}) => {
  const { open, record } = context;
  const [form] = Form.useForm();

  useEffect(() => {
    if (open && record) {
      form.setFieldsValue({ orgId: record.orgId });
    }
  }, [open, record, form]);

  const handleOk = useCallback(async (): Promise<void> => {
    const values = await form.validateFields();
    await setUserRole({
      username: record?.username ?? '',
      orgId: values.orgId,
      role: values.role,
    });
    message.success('角色已变更');
    onOk();
  }, [form, record, onOk]);

  return (
    <Drawer
      title={`改角色 — ${record?.username ?? ''}`}
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="orgId"
          label="组织"
          rules={[{ required: true, message: '请选择组织' }]}
        >
          <Select options={orgOptions} placeholder="选择组织" showSearch={{ filterOption }} />
        </Form.Item>
        <Form.Item
          name="role"
          label="新角色"
          rules={[{ required: true, message: '请选择新角色' }]}
        >
          <Select options={roleOptions} placeholder="选择新角色" />
        </Form.Item>
      </Form>
    </Drawer>
  );
};

export default AssignRolesDrawer;
