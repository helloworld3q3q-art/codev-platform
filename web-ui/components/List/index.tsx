import { Empty } from 'antd';
import React from 'react';

interface ListProps<T = any> {
  dataSource?: T[];
  renderItem: (item: T, index: number) => React.ReactNode;
  size?: 'small' | 'default' | 'large';
  split?: boolean;
  bordered?: boolean;
  loading?: boolean;
  locale?: {
    emptyText?: React.ReactNode;
  };
  style?: React.CSSProperties;
  className?: string;
}

const List = <T,>({
  dataSource = [],
  renderItem,
  size = 'default',
  split = true,
  bordered = false,
  loading = false,
  locale,
  style,
  className,
}: ListProps<T>) => {
  const border = 'border-#eaecf0 border-1 rounded-6 border-solid';
  if (loading) {
    return (
      <div style={style} className={`${className || ''} ${bordered ? border : ''}`}>
        <div className="text-center p-20">加载中...</div>
      </div>
    );
  }

  if (!dataSource.length) {
    return (
      <div style={style} className={`${className || ''} ${bordered ? border : ''}`}>
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={locale?.emptyText || '暂无数据'} />
      </div>
    );
  }

  return (
    <div style={style} className={`${className || ''} ${bordered ? border : ''}`}>
      {dataSource.map((item, index) => {
        const isLastItem = index === dataSource.length - 1;

        // 根据不同条件组合类名，确保所有类名都是静态的
        let itemClassName = '';

        // 添加尺寸类名
        if (size === 'small') {
          itemClassName += 'py-4';
        } else if (size === 'large') {
          itemClassName += 'py-12';
        } else {
          itemClassName += 'py-8';
        }

        // 添加分割线类名
        if (split && !isLastItem) {
          itemClassName += ' border-b-1 border-#f0f0f0 mb-6';
        }

        return (
          <div key={index} className={itemClassName}>
            {renderItem(item, index)}
          </div>
        );
      })}
    </div>
  );
};

export default List;
