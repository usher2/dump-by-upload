USE `dumpby`;
CREATE TABLE IF NOT EXISTS `auth` (
        `token` CHAR(64) NOT NULL UNIQUE,
        `type` TINYINT(1) NOT NULL DEFAULT 0,
        `nick` CHAR(64) NOT NULL,
        PRIMARY KEY (`token`),
        INDEX (`type`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `dumps` (
        `id` CHAR(64) NOT NULL UNIQUE,
        `crc` CHAR(64) NOT NULL,
        `a` TINYINT(1) NOT NULL DEFAULT 0,
        `ut` INT NOT NULL,
        `s` INT NOT NULL,
        `u` INT NOT NULL,
        `ct` INT NOT NULL DEFAULT 0,
        PRIMARY KEY  (`id`),
        INDEX (`ut`),
        INDEX (`crc`),
        INDEX (`a`),
        INDEX (`ct`)
) ENGINE=InnoDB;

