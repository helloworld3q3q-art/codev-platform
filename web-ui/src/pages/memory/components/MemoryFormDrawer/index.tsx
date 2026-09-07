import Drawer from '@/components/Drawer';
import { Form, Input, InputNumber, message, Select } from 'antd';
import { useCallback, useEffect } from 'react';

import { createMemory } from '../../services';
import type { MemoryScope } from '../../types';
import { SCOPE_OPTIONS } from '../../types';

export interface MemoryFormDrawerContext {
  open: boolean;
  scope: MemoryScope;
}

interface MemoryFormDrawerProps {
  context: MemoryFormDrawerContext;
  kindOptions: { label: string; value: string }[];
  onOk: () => void;
  onCancel: () => void;
}

const MemoryFormDrawer: React.FC<MemoryFormDrawerProps> = ({
  context,
  kindOptions,
  onOk,
  onCancel,
}) => {
  const { open, scope } = context;
  const [form] = Form.useForm();

  // 个人 scope 的 scopeRef 不让用户填 (默认本人, 后端强制)。
  const showScopeRef = scope !== 'personal';

  useEffect(() => {
    if (!open) {
      return;
    }
    form.resetFields();
    form.setFieldValue('scope', scope);
  }, [open, scope, form]);

  const handleOk = useCallback(async (): Promise<void> => {
    const values = await form.validateFields();
    const payload: Partial<API.MemoryWriteRequest> = {
      scope,
      content: values.content,
      kind: values.kind,
      topicKey: values.topicKey,
      ttl: values.ttl,
    };
    // 非个人 scope 才透传 scopeRef; 个人由后端强制为本人。
    if (showScopeRef) {
      payload.scopeRef = values.scopeRef;
    }
    await createMemory(payload);
    message.success('记忆写入成功');
    onOk();
  }, [form, scope, showScopeRef, onOk]);

  return (
    <Drawer
      title="写入记忆"
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      size="large"
      destroyOnHidden
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item name="scope" label="作用域">
          <Select options={SCOPE_OPTIONS} disabled />
        </Form.Item>
        {showScopeRef && (
          <Form.Item
            name="scopeRef"
            label="作用域 ref"
            rules={[{ required: true, message: '请输入作用域 ref' }]}
          >
            <Input placeholder="org / team_id / project_id" maxLength={128} />
          </Form.Item>
        )}
        <Form.Item
          name="content"
          label="记忆内容"
          rules={[{ required: true, message: '请输入记忆内容' }]}
        >
          <Input.TextArea placeholder="记忆内容" rows={4} maxLength={2000} showCount />
        </Form.Item>
        <Form.Item name="kind" label="类型">
          <Select options={kindOptions} placeholder="选择类型 (可留空)" allowClear />
        </Form.Item>
        <Form.Item name="topicKey" label="冲突检测键">
          <Input placeholder="topicKey (可留空)" maxLength={128} />
        </Form.Item>
        <Form.Item name="ttl" label="存活秒数">
          <InputNumber
            className="w-full"
            placeholder="省略=永久"
            min={0}
            controls={false}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
};

export default MemoryFormDrawer;
