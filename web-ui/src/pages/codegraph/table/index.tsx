// CodeGraph 表格页 — 上方搜索 + 下方表格列表。
// codegraph-api MVP 不支持分页，单次 fetch limit=200，前端纯展示（不走 ResizableTable 远程分页）。

import PageContainer from '@/components/PageContainer';
import { ProTable } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { Alert, Button, Card, Col, Input, Row, Select, Space } from 'antd';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { postSearch } from '@/services/apis/graphapi';

import type { NodeDTO } from '../common/types';
import { KIND_OPTIONS, LANG_OPTIONS } from '../common/utils';
import { createColumns } from './components/Columns';

const CodeGraphTablePage: React.FC = () => {
  // 订阅当前项目, 切项目后若有进行中的搜索则原地重拉(fetch 注入 X-Project-Id)。
  const { currentProjectId } = useModel('project');
  const [keyword, setKeyword] = useState('');
  const [languages, setLanguages] = useState<string[]>([]);
  const [kinds, setKinds] = useState<string[]>([]);
  const [items, setItems] = useState<NodeDTO[]>([]);
  const [loading, setLoading] = useState(false);

  const loadList = useCallback(async (): Promise<void> => {
    if (!keyword.trim()) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const res = await postSearch({
        keyword: keyword.trim(),
        languages: languages.length ? languages : undefined,
        kinds: kinds.length ? kinds : undefined,
        limit: 200,
      });
      setItems(res.data?.items ?? []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
    // currentProjectId 变化触发重拉
  }, [keyword, languages, kinds, currentProjectId]);

  // loadList 经 ref 透传, 让"切项目重搜"的 effect 只依赖 currentProjectId,
  // 避免把 loadList 放进依赖导致每次输入关键字都重搜。
  const loadListRef = useRef(loadList);
  loadListRef.current = loadList;

  // 切项目后, 若已有搜索关键字则原地重新搜索(无关键字 loadList 内部会清空)。
  useEffect(() => {
    loadListRef.current();
  }, [currentProjectId]);

  const handleKeywordChange = useCallback((e: React.ChangeEvent<HTMLInputElement>): void => {
    setKeyword(e.target.value);
  }, []);

  const handleLanguagesChange = useCallback((v: string[]): void => {
    setLanguages(v);
  }, []);

  const handleKindsChange = useCallback((v: string[]): void => {
    setKinds(v);
  }, []);

  const handleSearch = useCallback((): void => {
    loadList();
  }, [loadList]);

  const handleReset = useCallback((): void => {
    setKeyword('');
    setLanguages([]);
    setKinds([]);
    setItems([]);
  }, []);

  const columns = useMemo(() => createColumns(), []);

  const rowKey = useCallback(
    (record: NodeDTO): string => record.id ?? Math.random().toString(36),
    [],
  );

  return (
    <PageContainer>
      <Card classNames={{ root: 'i:mb-16' }} title="搜索条件">
        <Row gutter={12}>
          <Col span={8}>
            <Input
              placeholder="关键字（类名 / 方法 / 文件名）"
              value={keyword}
              onChange={handleKeywordChange}
              onPressEnter={handleSearch}
              allowClear
            />
          </Col>
          <Col span={6}>
            <Select
              mode="multiple"
              placeholder="语言（可多选）"
              value={languages}
              onChange={handleLanguagesChange}
              options={LANG_OPTIONS}
              className="w-full"
              maxTagCount="responsive"
              allowClear
            />
          </Col>
          <Col span={6}>
            <Select
              mode="multiple"
              placeholder="节点类型（可多选）"
              value={kinds}
              onChange={handleKindsChange}
              options={KIND_OPTIONS}
              className="w-full"
              maxTagCount="responsive"
              allowClear
            />
          </Col>
          <Col span={4}>
            <Space>
              <Button type="primary" onClick={handleSearch} loading={loading}>
                搜索
              </Button>
              <Button onClick={handleReset}>重置</Button>
            </Space>
          </Col>
        </Row>
      </Card>

      <Alert
        type="info"
        showIcon
        title="CodeGraph 表格视图"
        description="MVP 阶段单次最多返回 200 条；如需查看大批量，请细化关键字或类型过滤。"
        className="i:mb-16"
      />

      <ProTable<NodeDTO>
        rowKey={rowKey}
        columns={columns}
        dataSource={items}
        loading={loading}
        search={false}
        toolBarRender={false}
        options={false}
        pagination={{ defaultPageSize: 20, showSizeChanger: true }}
        scroll={{ x: 1400 }}
        locale={{ emptyText: keyword ? '无匹配结果' : '请输入关键字开始搜索' }}
      />
    </PageContainer>
  );
};

export default CodeGraphTablePage;
