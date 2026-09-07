import { Input } from 'antd';
import { useCallback, useState } from 'react';

interface ScopeRefSearchProps {
  onSearch: (value: string) => void;
}

// 作用域 ref 搜索框: 自管输入态, 回车 / 点击搜索时上抛, 避免 index 内联回调。
const ScopeRefSearch: React.FC<ScopeRefSearchProps> = ({ onSearch }) => {
  const [value, setValue] = useState('');

  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setValue(e.target.value);
  }, []);

  return (
    <Input.Search
      placeholder="作用域 ref (个人可留空)"
      allowClear
      className="w-280"
      value={value}
      onChange={handleChange}
      onSearch={onSearch}
    />
  );
};

export default ScopeRefSearch;
