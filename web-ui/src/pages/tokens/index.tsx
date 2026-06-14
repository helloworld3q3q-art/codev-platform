import { showConfirm } from '@/components/Modal';
import PageContainer from '@/components/PageContainer';
import ResizableTable from '@/components/ResizableTable';
import { postRevoke, postTokensList } from '@/services/apis/tokenapi';
import { getSelections } from '@/services/apis/userapi';
import { PlusOutlined } from '@ant-design/icons';
import type { ActionType } from '@ant-design/pro-components';
import { Button, message } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { createColumns } from './components/Columns';
import IssueTokenDrawer, { type IssueTokenDrawerContext } from './components/IssueTokenDrawer';
import TokenResultModal, { type TokenResultModalContext } from './components/TokenResultModal';
import { STATUS_TEXT, type TokenRow } from './components/utils';

const ISSUE_DEFAULT: IssueTokenDrawerContext = { open: false };
const RESULT_DEFAULT: TokenResultModalContext = { open: false };

const TokensPage: React.FC = () => {
  const actionRef = useRef<ActionType>();
  const [issueCtx, setIssueCtx] = useState<IssueTokenDrawerContext>(ISSUE_DEFAULT);
  const [resultCtx, setResultCtx] = useState<TokenResultModalContext>(RESULT_DEFAULT);
  const [userOptions, setUserOptions] = useState<{ label: string; value: string }[]>([]);

  const loadUserOptions = useCallback(async (): Promise<void> => {
    try {
      const res = await getSelections();
      const list = res.data ?? [];
      setUserOptions(
        list.map((u: API.UserSelectionItem) => ({
          label: u.label ?? u.value ?? '',
          value: u.value ?? '',
        })),
      );
    } catch {
      setUserOptions([]);
    }
  }, []);

  const handleAdd = useCallback(() => {
    setIssueCtx({ open: true });
  }, []);

  const handleIssueOk = useCallback((result?: API.TokenIssueResult) => {
    setIssueCtx(ISSUE_DEFAULT);
    setResultCtx({ open: true, result });
    actionRef.current?.reload();
  }, []);

  const handleIssueCancel = useCallback(() => {
    setIssueCtx(ISSUE_DEFAULT);
  }, []);

  const handleResultClose = useCallback(() => {
    setResultCtx(RESULT_DEFAULT);
  }, []);

  const handleRevoke = useCallback((record: TokenRow) => {
    showConfirm({
      title: `确认吊销该令牌？`,
      content: `用户 ${record.userId} / 前缀 ${record.tokenHashPrefix} ・ 吊销后该令牌立即失效`,
      onOk: async () => {
        await postRevoke({ tokenHashPrefix: record.tokenHashPrefix ?? '' });
        message.success('令牌已吊销');
        actionRef.current?.reload();
      },
    });
  }, []);

  const columns = useMemo(
    () => createColumns({ context: { statusMap: STATUS_TEXT, onRevoke: handleRevoke } }),
    [handleRevoke],
  );

  // token list 是非分页 CommonResult[list]; 直调 postTokensList, org 过滤在后端。
  const handleTableRequest = useCallback(async () => {
    const res = await postTokensList({});
    return { data: res.data ?? [], success: true };
  }, []);

  const handleToolBarRender = useCallback(
    () => [
      <Button key="add" type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
        签发令牌
      </Button>,
    ],
    [handleAdd],
  );

  useEffect(() => {
    loadUserOptions();
  }, [loadUserOptions]);

  return (
    <PageContainer>
      <ResizableTable<TokenRow>
        actionRef={actionRef}
        rowKey="tokenHashPrefix"
        columns={columns}
        request={handleTableRequest}
        toolBarRender={handleToolBarRender}
        scroll={{ x: 1100 }}
        pagination={false}
        search={false}
      />
      <IssueTokenDrawer
        context={issueCtx}
        userOptions={userOptions}
        onOk={handleIssueOk}
        onCancel={handleIssueCancel}
      />
      <TokenResultModal context={resultCtx} onClose={handleResultClose} />
    </PageContainer>
  );
};

export default TokensPage;
