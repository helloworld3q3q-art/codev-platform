import Drawer from '@/components/Drawer';
import { Form, Input, message } from 'antd';
import { useCallback, useEffect } from 'react';

import { createOrg, updateOrg, type OrgRow } from '../utils';

export interface OrgFormDrawerContext {
  open: boolean;
  mode: 'create' | 'edit';
  record?: OrgRow;
}

interface OrgFormDrawerProps {
  context: OrgFormDrawerContext;
  onOk: () => void;
  onCancel: () => void;
}

const OrgFormDrawer: React.FC<OrgFormDrawerProps> = ({ context, onOk, onCancel }) => {
  const [form] = Form.useForm();
  const { open, mode, record } = context;

  const handleOk = useCallback(async (): Promise<boolean | void> => {
    const values = await form.validateFields();
    if (mode === 'create') {
      // 创建需 platform_admin, 后端会拦, 前端正常调。
      await createOrg(values);
      message.success('组织已创建');
    } else {
      await updateOrg({ ...values, code: record?.code });
      message.success('组织已更新');
    }
    onOk();
  }, [form, mode, record, onOk]);

  // 打开时按模式回填 / 清空表单。
  useEffect(() => {
    if (open) {
      if (mode === 'edit' && record) {
        form.setFieldsValue({
          code: record.code,
          name: record.name,
          description: record.description,
        });
      } else {
        form.resetFields();
      }
    }
  }, [open, mode, record, form]);

  const title = mode === 'create' ? '新增组织' : '编辑组织';

  return (
    <Drawer title={title} open={open} onCancel={onCancel} onOk={handleOk} size="large" destroyOnHidden>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="code"
          label="组织编码"
          rules={[{ required: true, message: '请输入组织编码' }]}
        >
          <Input
            placeholder="org_id slug (小写字母 / 数字 / 连字符)"
            maxLength={64}
            disabled={mode === 'edit'}
          />
        </Form.Item>
        <Form.Item
          name="name"
          label="组织名称"
          rules={[{ required: true, message: '请输入组织名称' }]}
        >
          <Input placeholder="请输入组织名称" maxLength={200} />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea placeholder="请输入描述" maxLength={500} rows={3} />
        </Form.Item>
      </Form>
    </Drawer>
  );
};

export default OrgFormDrawer;
