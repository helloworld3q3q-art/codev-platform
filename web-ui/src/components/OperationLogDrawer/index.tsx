import Drawer from '@/components/Drawer';
import ResizableTable, { requestWrapper } from '@/components/ResizableTable';
import { postBusinessOperationLogsPage } from '@/services/apis/businessoperationlogapi';
import type { ProColumns } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { useCallback, useMemo } from 'react';

export interface OperationLogDrawerContext {
  open: boolean;
  module?: string;
  recordKey?: string;
}

interface OperationLogDrawerProps {
  context: OperationLogDrawerContext;
  onCancel: () => void;
}

const getLogRowKey = (r: API.BusinessOperationLogPageListItemResponse) =>
  `${r.operationTime}-${r.operationUser}-${r.operationType}`;

const OperationLogDrawer: React.FC<OperationLogDrawerProps> = ({ context, onCancel }) => {
  const { open, module, recordKey } = context;
  const { getFormattedEnums } = useModel('enum');
  const operationTypeMap = getFormattedEnums('BusinessNodeOperationType');

  const columns = useMemo(
    (): ProColumns<API.BusinessOperationLogPageListItemResponse>[] => [
      {
        title: '操作时间',
        dataIndex: 'operationTime',
        width: 180,
        search: false,
        valueType: 'dateTime',
      },
      {
        title: '操作类型',
        dataIndex: 'operationType',
        width: 80,
        search: false,
        render: (_, record) => {
          return operationTypeMap[record.operationType ?? ''];
        },
      },
      {
        title: '操作人',
        dataIndex: 'operationUser',
        width: 120,
        search: false,
      },
      {
        title: '操作内容',
        dataIndex: 'operationContent',
        ellipsis: true,
        search: false,
      },
    ],
    [operationTypeMap],
  );

  const handleLogRequest = useCallback(
    async (params: Record<string, any>, sort: any, filter: any) => {
      if (!recordKey || !module) {
        return { data: [], success: true, total: 0 };
      }
      return requestWrapper(params, sort, filter, postBusinessOperationLogsPage, (p) => ({
        module,
        pageNumber: p.current ?? 1,
        pageSize: p.pageSize ?? 10,
        recordKey,
      }));
    },
    [module, recordKey],
  );

  return (
    <Drawer
      title="操作日志"
      open={open}
      onCancel={onCancel}
      footer={null}
      size="large"
      showOkButton={false}
    >
      {open && recordKey && module ? (
        <ResizableTable<API.BusinessOperationLogPageListItemResponse>
          rowKey={getLogRowKey}
          columns={columns}
          request={handleLogRequest}
          search={false}
          toolBarRender={false}
          options={false}
        />
      ) : null}
    </Drawer>
  );
};

export default OperationLogDrawer;
