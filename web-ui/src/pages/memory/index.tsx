import PageContainer from '@/components/PageContainer';
import ResizableTable from '@/components/ResizableTable';
import { PlusOutlined } from '@ant-design/icons';
import { useModel } from '@umijs/max';
import { Button, Segmented, Space } from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { isAdminRole } from '@/utils/role';

import { createColumns } from './components/Columns';
import MemoryFormDrawer, {
  type MemoryFormDrawerContext,
} from './components/MemoryFormDrawer';
import ScopeRefSearch from './components/ScopeRefSearch';
import { fetchMemoryList } from './services';
import type { MemoryRow, MemoryScope } from './types';
import { SCOPE_OPTIONS, WRITE_ADMIN_SCOPES } from './types';

const DEFAULT_LIMIT = 50;
const FORM_DEFAULT: MemoryFormDrawerContext = { open: false, scope: 'personal' };

const MemoryPage: React.FC = () => {
  // 状态 / 类型枚举走后端真值源 (useModel('enum')), 角色判定走 useModel('user')。
  const { getFormattedEnums, getEnumOptions } = useModel('enum');
  const { userInfo } = useModel('user');
  const [scope, setScope] = useState<MemoryScope>('personal');
  const [scopeRef, setScopeRef] = useState<string>('');
  const [list, setList] = useState<MemoryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [formCtx, setFormCtx] = useState<MemoryFormDrawerContext>(FORM_DEFAULT);

  const statusMap = useMemo(() => getFormattedEnums('MemoryStatusEnum'), [getFormattedEnums]);
  const kindMap = useMemo(() => getFormattedEnums('MemoryKindEnum'), [getFormattedEnums]);
  const kindOptions = useMemo(() => getEnumOptions('MemoryKindEnum'), [getEnumOptions]);

  // org/team 写入需管理员角色, 非管理员禁用写入按钮 (与菜单显隐同口径 isAdminRole; 后端兜底)。
  const writeDisabled = useMemo(
    () => WRITE_ADMIN_SCOPES.includes(scope) && !isAdminRole(userInfo.roles),
    [scope, userInfo.roles],
  );

  const loadList = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const data = await fetchMemoryList({ scope, scopeRef, limit: DEFAULT_LIMIT });
      setList(data);
    } catch {
      setList([]);
    } finally {
      setLoading(false);
    }
  }, [scope, scopeRef]);

  const handleScopeChange = useCallback((value: string | number) => {
    setScope(value as MemoryScope);
  }, []);

  const handleSearch = useCallback((value: string) => {
    setScopeRef(value);
  }, []);

  const handleAdd = useCallback(() => {
    setFormCtx({ open: true, scope });
  }, [scope]);

  const handleFormOk = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
    loadList();
  }, [loadList]);

  const handleFormCancel = useCallback(() => {
    setFormCtx(FORM_DEFAULT);
  }, []);

  const columns = useMemo(
    () => createColumns({ context: { statusMap, kindMap } }),
    [statusMap, kindMap],
  );

  const handleToolBarRender = useCallback(
    () => [
      <Button
        key="add"
        type="primary"
        icon={<PlusOutlined />}
        disabled={writeDisabled}
        onClick={handleAdd}
      >
        写入记忆
      </Button>,
    ],
    [writeDisabled, handleAdd],
  );

  useEffect(() => {
    loadList();
  }, [loadList]);

  return (
    <PageContainer>
      <Space className="i:mb-16" size={12} wrap>
        <Segmented value={scope} options={SCOPE_OPTIONS} onChange={handleScopeChange} />
        <ScopeRefSearch onSearch={handleSearch} />
      </Space>
      <ResizableTable<MemoryRow>
        rowKey="id"
        columns={columns}
        dataSource={list}
        loading={loading}
        toolBarRender={handleToolBarRender}
        scroll={{ x: 900 }}
        pagination={{ defaultPageSize: 20 }}
        search={false}
      />
      <MemoryFormDrawer
        context={formCtx}
        kindOptions={kindOptions}
        onOk={handleFormOk}
        onCancel={handleFormCancel}
      />
    </PageContainer>
  );
};

export default MemoryPage;
