import Drawer from '@/components/Drawer';
import { postIssue } from '@/services/apis/tokenapi';
import { Form, Input, message, Select } from 'antd';
import { useCallback, useEffect } from 'react';

const filterOption = (input: string, option?: { label?: string }) =>
  String(option?.label ?? '')
    .toLowerCase()
    .includes(input.toLowerCase());

export interface IssueTokenDrawerContext {
  open: boolean;
}

interface IssueTokenDrawerProps {
  context: IssueTokenDrawerContext;
  userOptions: { label: string; value: string }[];
  onOk: (result?: API.TokenIssueResult) => void;
  onCancel: () => void;
}

const IssueTokenDrawer: React.FC<IssueTokenDrawerProps> = ({
  context,
  userOptions,
  onOk,
  onCancel,
}) => {
  const { open } = context;
  const [form] = Form.useForm();

  useEffect(() => {
    if (open) {
      form.resetFields();
    }
  }, [open, form]);

  const handleOk = useCallback(async (): Promise<void> => {
    const values = await form.validateFields();
    // 直调生成接口; org_id 由后端取 session.org_id (绝不由前端传, 越权防线)。
    const res = await postIssue({
      targetUser: values.targetUser,
      projects: values.projects,
      label: values.label,
      expires: values.expires,
    });
    message.success('令牌已签发');
    onOk(res.data);
  }, [form, onOk]);

  return (
    <Drawer
      title="签发接入令牌"
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      size="large"
      destroyOnHidden
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          name="targetUser"
          label="归属用户"
          rules={[{ required: true, message: '请选择归属用户' }]}
        >
          <Select
            options={userOptions}
            placeholder="选择令牌归属用户 (须本组织成员)"
            showSearch={{ filterOption }}
          />
        </Form.Item>
        <Form.Item
          name="projects"
          label="项目权限"
          tooltip="'*' 全部项目 | 'pid1,pid2' 逗号分隔 | 留空=无项目权 (安全默认)"
        >
          <Input placeholder="* 或 pid1,pid2 (留空=无项目权)" maxLength={2000} />
        </Form.Item>
        <Form.Item name="label" label="备注">
          <Input placeholder="人读备注 (如 alice laptop)" maxLength={200} />
        </Form.Item>
        <Form.Item name="expires" label="有效期" tooltip="30d/12h/90m/45s; 留空=永久">
          <Input placeholder="30d / 12h / 90m (留空=永久)" maxLength={16} />
        </Form.Item>
      </Form>
    </Drawer>
  );
};

export default IssueTokenDrawer;
