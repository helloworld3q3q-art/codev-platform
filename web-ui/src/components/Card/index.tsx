import React from 'react';

interface Props {
  title?: string;
  children?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

const Card: React.FC<Props> = (props) => {
  const { title, children, className = '', style } = props;

  return (
    <div className={`${className}`} style={style}>
      {title && (
        <div className={`text-16 text-secondary font-bold bg-#FFFBEC py-15 px-8 mb-10`}>
          {title}
        </div>
      )}
      {children && <div>{children}</div>}
    </div>
  );
};

export default Card;
