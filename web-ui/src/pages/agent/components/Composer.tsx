import { SendOutlined } from '@ant-design/icons';
import { Button, Input } from 'antd';
import { useCallback, useState } from 'react';

interface ComposerProps {
  loading: boolean;
  onSend: (question: string) => void;
}

const Composer: React.FC<ComposerProps> = ({ loading, onSend }) => {
  const [value, setValue] = useState<string>('');

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>): void => {
    setValue(e.target.value);
  }, []);

  const handleSend = useCallback((): void => {
    const question = value.trim();
    if (!question || loading) {
      return;
    }
    onSend(question);
    setValue('');
  }, [value, loading, onSend]);

  const handlePressEnter = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
      if (e.shiftKey) {
        return;
      }
      e.preventDefault();
      handleSend();
    },
    [handleSend],
  );

  return (
    <div className="flex gap-8 p-12 border-t border-#f0f0f0">
      <Input.TextArea
        value={value}
        placeholder="输入问题, Enter 发送 / Shift+Enter 换行"
        autoSize={{ minRows: 1, maxRows: 4 }}
        onChange={handleChange}
        onPressEnter={handlePressEnter}
      />
      <Button
        type="primary"
        icon={<SendOutlined />}
        loading={loading}
        onClick={handleSend}
      >
        发送
      </Button>
    </div>
  );
};

export default Composer;
