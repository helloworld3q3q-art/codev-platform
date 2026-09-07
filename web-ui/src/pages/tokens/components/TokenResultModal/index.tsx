import Modal from '@/components/Modal';
import { Alert, Button, Typography } from 'antd';
import { useCallback } from 'react';

import { formatExpiry, formatProjects } from '../utils';

export interface TokenResultModalContext {
  open: boolean;
  result?: API.TokenIssueResult;
}

interface TokenResultModalProps {
  context: TokenResultModalContext;
  onClose: () => void;
}

const TokenResultModal: React.FC<TokenResultModalProps> = ({ context, onClose }) => {
  const { open, result } = context;
  const token = result?.token ?? '';
  const handleClose = useCallback(() => onClose(), [onClose]);

  return (
    <Modal title="令牌已签发" open={open} onCancel={handleClose} footer={null} width={520}>
      <Alert
        type="warning"
        showIcon
        title="明文令牌只显示这一次"
        description="关闭后将无法再次查看, 请立即复制保存。库内只存哈希。"
        className="mb-16"
      />
      <div className="mb-8 text-13 text-#666">
        归属: {result?.userId} @ {result?.orgId} ・ 项目: {formatProjects(result?.projects)} ・ 到期:{' '}
        {formatExpiry(result?.expiresAt)}
      </div>
      <Typography.Paragraph
        copyable={{ text: token, tooltips: ['复制令牌', '已复制'] }}
        className="p-12 bg-#f5f5f5 rounded-6 break-all"
      >
        {token}
      </Typography.Paragraph>
      <div className="mt-12 text-13 text-#666">客户端接入 (二选一):</div>
      <Typography.Paragraph
        copyable={{ text: `export PLATFORM_TOKEN='${token}'` }}
        className="mt-4 p-12 bg-#f0f9eb rounded-6 break-all text-13"
      >
        export PLATFORM_TOKEN=&apos;&lt;令牌&gt;&apos; &nbsp;+&nbsp; codev-platform gateway client-auth
      </Typography.Paragraph>
      <Typography.Paragraph
        copyable={{ text: `Authorization: Bearer ${token}` }}
        className="mt-4 p-12 bg-#f0f9eb rounded-6 break-all text-13"
      >
        .mcp.json 各 server headers: Authorization: Bearer &lt;令牌&gt;
      </Typography.Paragraph>
      <div className="mt-16 text-right">
        <Button type="primary" onClick={handleClose}>
          我已保存
        </Button>
      </div>
    </Modal>
  );
};

export default TokenResultModal;
