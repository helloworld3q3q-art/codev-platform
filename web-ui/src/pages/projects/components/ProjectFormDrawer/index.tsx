import Drawer from '@/components/Drawer';
import { Form, Input, message } from 'antd';
import { useCallback, useEffect } from 'react';

import { registerProject, type ProjectRegisterParams } from '../utils';

export interface ProjectFormDrawerContext {
  open: boolean;
}

interface ProjectFormDrawerProps {
  context: ProjectFormDrawerContext;
  onOk: () => void;
  onCancel: () => void;
}

const ProjectFormDrawer: React.FC<ProjectFormDrawerProps> = ({ context, onOk, onCancel }) => {
  const [form] = Form.useForm<ProjectRegisterParams>();
  const { open } = context;

  const handleOk = useCallback(async (): Promise<boolean | void> => {
    const values = await form.validateFields();
    await registerProject(values);
    message.success('已注册');
    onOk();
  }, [form, onOk]);

  // 打开时清空表单
  useEffect(() => {
    if (open) {
      form.resetFields();
    }
  }, [open, form]);

  return (
    <Drawer
      title="新增项目"
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      size="large"
      destroyOnHidden
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="code"
          label="项目编码"
          rules={[{ required: true, message: '请输入项目编码' }]}
        >
          <Input placeholder="project_id slug (小写字母 / 数字 / 连字符)" maxLength={64} />
        </Form.Item>
        <Form.Item
          name="name"
          label="项目名称"
          rules={[{ required: true, message: '请输入项目名称' }]}
        >
          <Input placeholder="请输入项目名称" maxLength={200} />
        </Form.Item>
        <Form.Item name="repoPath" label="仓路径">
          <Input placeholder="请输入项目仓路径" maxLength={500} />
        </Form.Item>
      </Form>
    </Drawer>
  );
};

export default ProjectFormDrawer;
