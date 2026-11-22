-- =====================================================================
-- LUME Skin - Estrutura do banco de dados (PostgreSQL)
-- =====================================================================
 
-- Este arquivo existe para leitura e consulta da estrutura.

-- Ordem de execucao (se for rodar manualmente):
--   01_estrutura.sql -> 02_dados_produtos.sql -> 03_dados_regras_tratamento.sql
--
-- Tabelas:
--   usuario .............. contas de usuario
--   perfil_dermatologico . respostas do questionario + ultima analise facial
--   passo_rotina_feito ... historico dos passos da rotina marcados como feitos
--   produto .............. catalogo de produtos de skincare
--   regra_tratamento ..... regras de ativos por perfil (dataset do Kaggle)
--   artigo ............... artigos cientificos (gerenciados pelo administrador)
--   especialista ......... profissionais (gerenciados pelo administrador)
-- =====================================================================


-- ---------------------------------------------------------------------
-- USUARIO
-- Conta do usuario
-- ---------------------------------------------------------------------
CREATE TABLE usuario (
    id_usuario     SERIAL       PRIMARY KEY,
    nome_usuario   VARCHAR(100) NOT NULL,
    email          VARCHAR(200) NOT NULL UNIQUE,
    senha_usuario  VARCHAR(100) NOT NULL,          
    data_cadastro  TIMESTAMPTZ  NOT NULL
);

-- ---------------------------------------------------------------------
-- PERFIL_DERMATOLOGICO
-- Guarda as respostas do questionario e o resultado da ultima analise facial.
-- ---------------------------------------------------------------------
CREATE TABLE perfil_dermatologico (
    id                          SERIAL       PRIMARY KEY,
    id_usuario                  INTEGER      NOT NULL UNIQUE
                                REFERENCES usuario (id_usuario) ON DELETE CASCADE,
    idade                       INTEGER      NOT NULL,
    tipo_pele                   VARCHAR(10)  NOT NULL,
    alergias                    TEXT,
    objetivo                    VARCHAR(200) NOT NULL, 
    preferencia_produto         VARCHAR(10)  NOT NULL,  
    usa_maquiagem_diariamente   VARCHAR(10)  NOT NULL,   
    sono_horas                  VARCHAR(10)  NOT NULL DEFAULT 'adequado',   
    exercicio_frequencia        VARCHAR(10)  NOT NULL DEFAULT '1-2x',      
    protetor_solar              INTEGER      NOT NULL DEFAULT 1,          
    sensibilidade               BOOLEAN      NOT NULL DEFAULT FALSE,
    gestante                    BOOLEAN      NOT NULL DEFAULT FALSE,
    tipo_acne                   VARCHAR(20)  NOT NULL DEFAULT '', 
    fototipo                    INTEGER,                
    porcentagem_saude           INTEGER      NOT NULL,  
    url_mapeamento_facial       VARCHAR(500),           
    dados_ia                    TEXT                 
);

-- ---------------------------------------------------------------------
-- PASSO_ROTINA_FEITO
-- Historico: cada linha e um passo da rotina marcado como feito em um dia.
-- ---------------------------------------------------------------------
CREATE TABLE passo_rotina_feito (
    id           SERIAL       PRIMARY KEY,
    id_usuario   INTEGER      NOT NULL
                 REFERENCES usuario (id_usuario) ON DELETE CASCADE,
    data         DATE         NOT NULL,
    periodo      VARCHAR(10)  NOT NULL,                 
    titulo       VARCHAR(50)  NOT NULL,                  
    produto_id   INTEGER      NOT NULL DEFAULT 0,      
    marcado_em   TIMESTAMPTZ  NOT NULL,
    UNIQUE (id_usuario, data, periodo, titulo, produto_id)
);

-- ---------------------------------------------------------------------
-- PRODUTO
-- Catalogo importado de core/data/00_produtos_skincare.csv.
-- tipo_pele e preocupacao guardam varios valores separados por ";".
-- ---------------------------------------------------------------------
CREATE TABLE produto (
    id                     SERIAL        PRIMARY KEY,
    marca                  VARCHAR(100)  NOT NULL,
    nome                   VARCHAR(200)  NOT NULL,
    categoria              VARCHAR(30)   NOT NULL,      
    tipo_pele              VARCHAR(100)  NOT NULL,       
    preocupacao            VARCHAR(255)  NOT NULL,       
    modo_uso               VARCHAR(20)   NOT NULL,     
    ingrediente_principal  VARCHAR(100)  NOT NULL,
    fps                    SMALLINT      CHECK (fps >= 0),
    tamanho_ml             INTEGER       NOT NULL CHECK (tamanho_ml >= 0),
    preco                  NUMERIC(7,2),
    avaliacao              NUMERIC(2,1),
    num_avaliacoes         INTEGER       CHECK (num_avaliacoes >= 0),
    fonte                  VARCHAR(100)  NOT NULL DEFAULT '',  
    descricao              TEXT          NOT NULL DEFAULT '',
    imagem_url             VARCHAR(300)  NOT NULL DEFAULT '',
    link_produto           VARCHAR(300)  NOT NULL DEFAULT '',
    slug                   VARCHAR(250)  NOT NULL UNIQUE
);

-- ---------------------------------------------------------------------
-- REGRA_TRATAMENTO
-- 1.120 regras do dataset do Kaggle
-- ---------------------------------------------------------------------
CREATE TABLE regra_tratamento (
    id             SERIAL       PRIMARY KEY,
    faixa_etaria   VARCHAR(10)  NOT NULL,               
    tipo_pele      VARCHAR(10)  NOT NULL,               
    subtipo_pele   VARCHAR(50)  NOT NULL,                
    sensibilidade  BOOLEAN      NOT NULL,
    preocupacao    VARCHAR(50)  NOT NULL,                
    tipo_interno   VARCHAR(30)  NOT NULL DEFAULT '',    
    ingredientes   VARCHAR(200) NOT NULL,             
    efeitos        TEXT         NOT NULL DEFAULT '',
    UNIQUE (faixa_etaria, tipo_pele, subtipo_pele, sensibilidade, preocupacao, tipo_interno)
);

-- ---------------------------------------------------------------------
-- ARTIGO  (gerenciado pelo administrador)
-- ---------------------------------------------------------------------
CREATE TABLE artigo (
    id           SERIAL        PRIMARY KEY,
    titulo       VARCHAR(200)  NOT NULL,
    autor        VARCHAR(150)  NOT NULL,
    ano          INTEGER       NOT NULL,
    resumo       TEXT,
    url_capa     VARCHAR(300),
    url_leitura  VARCHAR(300)  NOT NULL
);


-- ---------------------------------------------------------------------
-- ESPECIALISTA  (gerenciado pelo administrador)
-- ---------------------------------------------------------------------
CREATE TABLE especialista (
    id                 SERIAL        PRIMARY KEY,
    nome               VARCHAR(150)  NOT NULL,
    especialidade      VARCHAR(100)  NOT NULL,           
    crm                VARCHAR(30),
    telefone_whatsapp  VARCHAR(20)   NOT NULL,          
    url_foto           VARCHAR(300)
);


-- ---------------------------------------------------------------------
-- Indices de apoio (o Django ja cria os de chave unica acima)
-- ---------------------------------------------------------------------
CREATE INDEX idx_passo_rotina_usuario ON passo_rotina_feito (id_usuario);
